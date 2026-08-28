"""Action-handler mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

from robotsix_auto_mail.config import (
    APP_CLASSIFIER,
    resolve_llm_api_key,
    resolve_llm_tier,
)
from robotsix_auto_mail.core._constants import _WATERMARK_RUNNING
from robotsix_auto_mail.db import MailRecord, get_watermark, set_watermark
from robotsix_auto_mail.server._constants import _is_safe_redirect_path, _with_db
from robotsix_auto_mail.triage import (
    TO_ARCHIVE,
    TO_CALENDAR,
    VALID_TRIAGE_ACTIONS,
    propose_archive_subfolder_llm,
    set_triage_decision,
)

logger = logging.getLogger(__name__)


def _json_field_value(data: dict[str, Any], field: str) -> str:
    """Return *field* from *data* coerced to ``str``.

    ``None`` / missing keys yield the empty string so that
    downstream validation can produce a clear error message
    rather than a spurious ``"None"`` literal.
    """
    val = data.get(field)
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    return str(val)


class _BoardActionMixin:
    """Mixin providing POST action handlers for the board server."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    def _launch_background_worker(
        self,
        watermark_key: str,
        target: Callable[..., None] | None = None,
        args: tuple[Any, ...] = (),
        *,
        running_check: Callable[[str | None], bool] | None = None,
        precheck: Callable[[Any], bool] | None = None,
        db_path: str | None = None,
        redirect: bool = True,
    ) -> bool:
        """Acquire a single-flight watermark and optionally spawn a daemon thread.

        Returns ``True`` when the watermark was acquired (and, when *target*
        is not ``None``, the worker thread was started).  Returns ``False``
        when the watermark is already held or *precheck* returns ``False``.

        When *redirect* is ``True`` (the default) the handler redirects to
        ``/board`` on both the failure paths **and** after a successful
        spawn.  Set *redirect* to ``False`` when the caller needs to
        control the response itself (e.g. in an aggregate fan-out loop).
        """
        _path = db_path if db_path is not None else self.db_path

        with _with_db(_path) as conn:
            if precheck is not None and not precheck(conn):
                if redirect:
                    self._redirect("/board", code=302)
                return False

            if running_check is not None:
                _is_running = running_check
            else:

                def _is_running(s: str | None) -> bool:
                    return s == _WATERMARK_RUNNING

            if _is_running(get_watermark(conn, watermark_key)):
                if redirect:
                    self._redirect("/board", code=302)
                return False

            set_watermark(conn, watermark_key, _WATERMARK_RUNNING)

        if target is not None:
            threading.Thread(target=target, args=args, daemon=True).start()
            if redirect:
                self._redirect("/board", code=302)

        return True

    def _parse_request_body(
        self, *fields: str, no_strip: frozenset[str] = frozenset()
    ) -> dict[str, str]:
        """Parse the request body as URL-encoded form data.

        Returns a dict mapping each requested *field* name to its
        first value.  Values are stripped of leading/trailing
        whitespace *unless* the field name appears in *no_strip*.

        When form parsing yields only empty values, falls back to
        JSON parsing so that clients sending ``Content-Type:
        application/json`` receive the same behaviour.
        """
        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        parsed = parse_qs(raw)
        result = {
            field: (
                (parsed.get(field) or [""])[0].strip()
                if field not in no_strip
                else (parsed.get(field) or [""])[0]
            )
            for field in fields
        }

        # JSON fallback: when form parsing yields only empty values,
        # try to parse the body as JSON.
        if all(v == "" for v in result.values()):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(data, dict):
                    result = {
                        field: (
                            _json_field_value(data, field).strip()
                            if field not in no_strip
                            else _json_field_value(data, field)
                        )
                        for field in fields
                    }
        return result

    def _handle_post_action(
        self,
        *fields: str,
        action: Any,
        no_strip: frozenset[str] = frozenset(),
    ) -> None:
        """Shared POST handler skeleton.

        1. Parses the request body for the declared *fields*.
        2. Validates ``message_id`` (returns 400 if missing).
        3. Opens a read-only DB connection, looks up the record,
           and returns 404 if absent.
        4. Delegates to *action(conn, record, redirect_to,
           \\*\\*extra_fields)* for the handler-specific logic.
           When *action* returns ``False`` the redirect is skipped
           (the callback already sent a response).
        5. Closes the connection and performs a safe redirect.
        """
        from robotsix_auto_mail.db import get_record_by_message_id

        f = self._parse_request_body(*fields, no_strip=no_strip)
        message_id = f.get("message_id", "")
        redirect_to = f.get("redirect_to", "")

        if not message_id:
            content_type = self.headers.get("Content-Type", "")
            if isinstance(content_type, str) and "application/json" in content_type:
                self._bad_request("Malformed JSON body")
            else:
                self._bad_request("Missing message_id")
            return

        with _with_db(self.db_path) as conn:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._not_found()
                return

            extra = {
                k: v for k, v in f.items() if k not in ("message_id", "redirect_to")
            }
            if action(conn, record, redirect_to, **extra) is False:
                return

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
            self._redirect("/board", code=302)

    def _handle_move(self) -> None:
        """Process POST /move — update a card's triage decision and redirect."""

        def move_action(
            conn: Any, record: MailRecord, redirect_to: str, triage_action: str
        ) -> bool:
            if not triage_action:
                self._bad_request("Missing triage_action")
                return False
            if triage_action not in VALID_TRIAGE_ACTIONS:
                self._bad_request(f"Invalid triage action: {triage_action!r}")
                return False

            message_id = record.message_id

            try:
                set_triage_decision(
                    conn,
                    message_id,
                    triage_action,
                    source="user",
                    reason=f"moved to {triage_action}",
                )
            except sqlite3.IntegrityError:
                # Defense in depth: a stale CHECK constraint (legacy DB
                # predating a new triage action) makes the upsert raise
                # IntegrityError.  Persisting the decision is impossible,
                # but the move must not crash the worker into a 502 — send
                # a clean error response and skip the success redirect.
                self._bad_request(f"Could not move to {triage_action}")
                return False

            if triage_action == TO_ARCHIVE:
                try:
                    if self.mail_config is not None:
                        classifier_level, classifier_model = resolve_llm_tier(
                            APP_CLASSIFIER
                        )
                        propose_archive_subfolder_llm(
                            conn,
                            record,
                            resolve_llm_api_key(raise_on_missing=False),
                            provider_model=classifier_model or None,
                            level=classifier_level,
                            rules=self.mail_config.triage_guidance,
                        )
                except Exception:  # noqa: S110  # nosec B110
                    pass  # Non-fatal: board falls back to deterministic proposal
            elif triage_action == TO_CALENDAR:
                import uuid

                from robotsix_auto_mail.db import (
                    update_calendar_correlation_id,
                    update_calendar_event_ref,
                )

                correlation_id = str(uuid.uuid4())
                try:
                    update_calendar_correlation_id(conn, message_id, correlation_id)
                    update_calendar_event_ref(conn, message_id, "pending")
                except Exception:  # noqa: S110  # nosec B110
                    pass  # Non-fatal: calendar write is best-effort
            return True

        self._handle_post_action(
            "message_id",
            "triage_action",
            "redirect_to",
            action=move_action,
        )

    def _handle_delete(self) -> None:
        """Process POST /delete — delete mail from IMAP mailbox and local DB."""
        from robotsix_auto_mail.db import delete_record_by_message_id

        def delete_action(conn: Any, record: MailRecord, redirect_to: str) -> bool:
            # -- IMAP deletion (when config and UID are both available) --
            if self.mail_config is not None and record.imap_uid is not None:
                from robotsix_auto_mail.imap import (
                    ImapClient,
                    ImapError,
                    ImapMessageNotFoundError,
                    resolve_uid_with_fallback,
                )

                try:
                    with ImapClient(self.mail_config) as client:
                        resolved_uid = resolve_uid_with_fallback(
                            client,
                            record.source_folder,
                            record.imap_uid,
                            record.message_id,
                        )
                        client.delete_message(resolved_uid)
                except ImapMessageNotFoundError:
                    from robotsix_auto_mail.server.adapters import (
                        _imap_cross_folder_fallback,
                    )

                    try:
                        result = _imap_cross_folder_fallback(
                            self.mail_config, record, conn
                        )
                    except (ImapError, OSError) as exc:
                        self._send_response(
                            f"IMAP cross-folder resolution failed: {exc}",
                            status=502,
                        )
                        return False
                    if result is not None:
                        new_folder, new_uid = result
                        try:
                            with ImapClient(self.mail_config) as client2:
                                client2.select_folder(new_folder)
                                client2.delete_message(new_uid)
                        except (ImapError, OSError) as exc:
                            self._send_response(
                                f"IMAP cross-folder resolution failed: {exc}",
                                status=502,
                            )
                            return False
                except (ImapError, OSError) as exc:
                    self._send_response(
                        f"IMAP deletion failed: {exc}",
                        status=502,
                    )
                    return False

            # -- compose-draft fallback: no UID but message may exist ----
            # Compose-drafts whose APPENDUID was not returned by the
            # server (no UIDPLUS) have imap_uid=None.  Search for
            # them by Message-ID in the Drafts folder and delete if
            # found; degrade gracefully if already gone.
            elif (
                self.mail_config is not None
                and record.imap_uid is None
                and record.message_id.startswith("<compose-")
            ):
                from robotsix_auto_mail.imap import ImapClient, ImapError

                try:
                    with ImapClient(self.mail_config) as client:
                        folders = client.list_folders()
                        drafts_folder: str | None = None
                        for folder_info in folders:
                            if any(
                                a.lower() == "\\drafts" for a in folder_info.attributes
                            ):
                                drafts_folder = folder_info.name
                                break
                        if drafts_folder is None:
                            for folder_info in folders:
                                if "draft" in folder_info.name.lower():
                                    drafts_folder = folder_info.name
                                    break
                        if drafts_folder is not None:
                            client.select_folder(drafts_folder)
                            found = client.search_uids(
                                f'HEADER Message-ID "{record.message_id}"'
                            )
                            if found:
                                client.delete_message(found[0])
                except Exception as exc:
                    # Draft may already have been removed manually —
                    # log and continue with the DB deletion.
                    logger.warning(
                        "Could not delete compose-draft %s from IMAP: %s",
                        record.message_id,
                        exc,
                    )

            # -- local DB deletion --
            delete_record_by_message_id(conn, record.message_id)
            return True

        self._handle_post_action(
            "message_id",
            "redirect_to",
            action=delete_action,
        )

    def _handle_save_notes(self) -> None:
        """Process POST /save-notes — persist notes for a mail record."""
        from robotsix_auto_mail.db import update_notes

        def save_notes_action(
            conn: Any, record: MailRecord, redirect_to: str, notes: str
        ) -> bool:
            update_notes(conn, record.message_id, notes)
            return True

        self._handle_post_action(
            "message_id",
            "redirect_to",
            "notes",
            no_strip=frozenset({"notes"}),
            action=save_notes_action,
        )
