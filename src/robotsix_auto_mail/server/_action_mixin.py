"""Action-handler mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

from robotsix_auto_mail.config import (
    APP_CLASSIFIER,
    DEFAULT_ARCHIVE_ROOT,
    MailConfig,
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
    record_user_action,
    rules_text_for,
    set_triage_decision,
)

logger = logging.getLogger(__name__)


def _find_message_in_archive(
    client: Any,
    message_id: str,
    archive_root: str,
    delimiter: str,
) -> tuple[str, int] | None:
    """Search all folders under *archive_root* for *message_id*.

    Returns ``(folder_name, uid)`` or ``None`` when not found.
    Only searches folders whose name starts with *archive_root*.
    Skips folders with ``\\Noselect`` attribute.
    """
    root_prefix = f"{archive_root}{delimiter}"
    for folder in client.list_folders():
        if any(
            attr.lower() == "\\noselect" for attr in folder.attributes
        ):
            continue
        if folder.name != archive_root and not folder.name.startswith(
            root_prefix
        ):
            continue
        client.select_folder(folder.name)
        uids = client.search_uids(f'HEADER Message-ID "{message_id}"')
        if uids:
            return (folder.name, uids[0])
    return None


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
                if self.mail_config is not None:
                    record_user_action(record, triage_action, config=self.mail_config)
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
                            rules=rules_text_for(self.mail_config),
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

            # -- local DB deletion --
            delete_record_by_message_id(conn, record.message_id)
            return True

        self._handle_post_action(
            "message_id",
            "redirect_to",
            action=delete_action,
        )

    def _imap_archive_move(
        self,
        mail_config: MailConfig,
        imap_uid: int,
        effective_root: str,
        subfolder: str | None,
        source_folder: str = "INBOX",
        message_id: str = "",
    ) -> None:
        """Move a message to the archive folder via IMAP.

        Selects *source_folder* (the record's origin folder) rather
        than assuming ``config.imap_folder``.  If the stored UID is
        stale, falls back to a ``HEADER Message-ID`` search before
        giving up.

        Raises ValueError on security-policy violations (caller should
        return 400).  Raises ImapError or OSError on IMAP/IO failures
        (caller should return 502).
        """
        from robotsix_auto_mail.imap import ImapClient, resolve_uid_with_fallback
        from robotsix_auto_mail.server.adapters import _archive_dest_folder

        with ImapClient(mail_config) as client:
            # Resolve the possibly-stale UID, selecting source_folder.
            resolved_uid = resolve_uid_with_fallback(
                client, source_folder, imap_uid, message_id
            )

            # Determine the IMAP hierarchy delimiter.
            existing = client.list_folders()
            delimiter = next(
                (f.delimiter for f in existing if f.delimiter),
                "/",
            )

            dest_folder = _archive_dest_folder(effective_root, subfolder, delimiter)
            if dest_folder is None:
                raise ValueError("Archive destination escapes archive root")

            # -- ensure destination folder hierarchy exists ----
            from robotsix_auto_mail.server.adapters import _ensure_folder_hierarchy

            _ensure_folder_hierarchy(client, dest_folder, delimiter)

            client.move_message(resolved_uid, dest_folder)

    def _archive_and_delete(self, conn: Any, record: MailRecord) -> bool:
        """Archive *record*'s message via IMAP, then delete its local row.

        Shared by :meth:`_handle_archive` and :meth:`_handle_send_draft`.
        Computes the effective archive root + subfolder, performs the IMAP
        move (only when IMAP is configured and the record has a tracked
        UID), writes an archive-audit-log entry, then removes the local
        database record.

        Returns ``True`` on success.  On a security-policy violation it
        sends a 400 and returns ``False``; on an IMAP/IO failure it sends a
        502 and returns ``False`` — in both error cases the local record is
        left intact.
        """
        from robotsix_auto_mail.db import (
            delete_record_by_message_id,
            write_archive_audit_entry,
        )
        from robotsix_auto_mail.triage import get_archive_subfolder_with_source

        # Compute the effective archive subfolder.
        classifier_level, _ = resolve_llm_tier(APP_CLASSIFIER)
        subfolder, proposal_source = get_archive_subfolder_with_source(
            conn,
            record.message_id,
            record,
            api_key=resolve_llm_api_key(raise_on_missing=False),
            level=classifier_level,
            rules=rules_text_for(self.mail_config),
        )

        # Determine the archive root.
        archive_root = (
            self.mail_config.archive_root
            if self.mail_config is not None
            else DEFAULT_ARCHIVE_ROOT
        )

        effective_root = archive_root

        # -- IMAP move phase (only when IMAP is configured and the
        #    record has a tracked UID) --
        if self.mail_config is not None and record.imap_uid is not None:
            from robotsix_auto_mail.imap import ImapError, ImapMessageNotFoundError

            try:
                self._imap_archive_move(
                    self.mail_config,
                    record.imap_uid,
                    effective_root,
                    subfolder,
                    source_folder=record.source_folder,
                    message_id=record.message_id,
                )
            except ValueError:
                logger.exception("Action handler failed")
                self._bad_request("Invalid request")
                return False
            except ImapMessageNotFoundError:
                from robotsix_auto_mail.server.adapters import (
                    _imap_cross_folder_fallback,
                )

                try:
                    result = _imap_cross_folder_fallback(self.mail_config, record, conn)
                except (ImapError, OSError) as exc:
                    self._send_response(
                        f"IMAP cross-folder resolution failed: {exc}",
                        status=502,
                    )
                    return False
                if result is not None:
                    new_folder, new_uid = result
                    try:
                        self._imap_archive_move(
                            self.mail_config,
                            new_uid,
                            effective_root,
                            subfolder,
                            source_folder=new_folder,
                            message_id=record.message_id,
                        )
                    except (ImapError, OSError) as exc:
                        self._send_response(
                            f"IMAP cross-folder resolution failed: {exc}",
                            status=502,
                        )
                        return False
                # Mail gone or healed — write audit entry and delete
                # the local record in both cases.
                with contextlib.suppress(Exception):
                    write_archive_audit_entry(
                        conn,
                        message_id=record.message_id,
                        subject=record.subject,
                        sender=record.sender,
                        date=record.date,
                        source_column=TO_ARCHIVE,
                        source_folder=record.source_folder,
                        dest_folder=subfolder,
                        proposal_source=proposal_source,
                    )
                delete_record_by_message_id(conn, record.message_id)
                return True
            except (ImapError, OSError) as exc:
                self._send_response(
                    f"IMAP archive failed: {exc}",
                    status=502,
                )
                return False

        # -- record the human-confirmed archive-folder choice (best-effort);
        #    updates the triage rules file via the flash LLM --
        if subfolder and self.mail_config is not None:
            with contextlib.suppress(Exception):
                # Non-fatal: rule maintenance is advisory only
                record_user_action(
                    record, TO_ARCHIVE, config=self.mail_config, subfolder=subfolder
                )

        # -- write audit-log entry before deleting the local row --
        with contextlib.suppress(Exception):
            # Non-fatal: archive succeeds even if audit write fails
            write_archive_audit_entry(
                conn,
                message_id=record.message_id,
                subject=record.subject,
                sender=record.sender,
                date=record.date,
                source_column=TO_ARCHIVE,
                source_folder=record.source_folder,
                dest_folder=subfolder,
                proposal_source=proposal_source,
            )

        # -- local DB cleanup --
        delete_record_by_message_id(conn, record.message_id)
        return True

    def _handle_archive(self) -> None:
        """Process POST /archive — move mail to archive folder via IMAP
        and remove it from the local database.
        """

        def archive_action(conn: Any, record: MailRecord, redirect_to: str) -> bool:
            return self._archive_and_delete(conn, record)

        self._handle_post_action(
            "message_id",
            "redirect_to",
            action=archive_action,
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

    def _handle_archive_move(self) -> None:
        """Process POST /archive-move — move a message between archive folders.

        Accepts a JSON body with:

        - ``message_id`` (str): the Message-ID header of the mail to move.
        - ``uid`` (int): the IMAP UID within ``source_folder`` (alternative
          to ``message_id``; at least one of ``message_id`` / ``uid`` required).
        - ``source_folder`` (str): the current archive subfolder path
          (required when ``uid`` is provided; used as a hint when only
          ``message_id`` is provided).
        - ``target_subfolder`` (str): the destination archive subfolder.

        Validates that both source and target are under the archive root.
        Creates the target folder hierarchy if needed (per the lazy-creation
        convention).  Returns JSON on success.
        """
        from robotsix_auto_mail.imap import (
            ImapClient,
            ImapError,
            ImapMessageNotFoundError,
        )

        # Parse the JSON body.
        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self._bad_request("Malformed JSON body")
            return

        if not isinstance(data, dict):
            self._bad_request("JSON body must be an object")
            return

        message_id = _json_field_value(data, "message_id")
        uid_raw = data.get("uid")
        source_folder = _json_field_value(data, "source_folder")
        target_subfolder = _json_field_value(data, "target_subfolder")

        uid: int | None = None
        if uid_raw is not None:
            try:
                uid = int(uid_raw)
            except (ValueError, TypeError):
                self._bad_request("uid must be an integer")
                return

        if not message_id and uid is None:
            self._bad_request(
                "At least one of message_id or uid is required"
            )
            return

        if not target_subfolder:
            self._bad_request("target_subfolder is required")
            return

        if self.mail_config is None:
            self._serve_json(
                {"error": "IMAP not configured for this account"},
                status=503,
            )
            return

        archive_root = self._effective_archive_root

        # Validate source_folder is under archive root (no ".." segments).
        if source_folder and ".." in source_folder.split("/"):
            self._bad_request("source_folder must not contain '..'")
            return

        # Validate target_subfolder is under archive root (no ".." segments).
        if ".." in target_subfolder.split("/"):
            self._bad_request("target_subfolder must not contain '..'")
            return

        # Validate that uid requires source_folder when message_id is absent.
        if uid is not None and not source_folder and not message_id:
            self._bad_request(
                "source_folder is required when uid is provided without message_id"
            )
            return

        try:
            with ImapClient(self.mail_config) as client:
                # Discover the server's hierarchy delimiter.
                existing = client.list_folders()
                delimiter = next(
                    (f.delimiter for f in existing if f.delimiter),
                    "/",
                )

                # Resolve the full source folder path.
                resolved_source_folder: str | None = None
                resolved_uid: int | None = None

                if uid is not None and source_folder:
                    # Explicit uid + source_folder: use directly.
                    translated_source = (
                        f"{archive_root}/{source_folder}".replace("/", delimiter)
                    )
                    # Validate under archive root.
                    root_prefix = f"{archive_root.replace('/', delimiter)}{delimiter}"
                    ar_translated = archive_root.replace("/", delimiter)
                    if (
                        translated_source != ar_translated
                        and not translated_source.startswith(root_prefix)
                    ):
                        self._bad_request(
                            "source_folder escapes archive root"
                        )
                        return
                    resolved_source_folder = translated_source
                    resolved_uid = uid
                else:
                    # message_id — search all archive folders.
                    assert message_id  # noqa: S101 # nosec B101 — guard: message_id must be set when uid/source_folder are absent
                    resolved = _find_message_in_archive(
                        client, message_id, archive_root, delimiter
                    )
                    if resolved is None:
                        self._not_found()
                        return
                    resolved_source_folder, resolved_uid = resolved

                if resolved_source_folder is None or resolved_uid is None:
                    self._not_found()
                    return

                # Compute the destination folder path.
                translated_target = (
                    f"{archive_root}/{target_subfolder}".replace("/", delimiter)
                )
                root_prefix = f"{archive_root.replace('/', delimiter)}{delimiter}"
                ar_translated = archive_root.replace("/", delimiter)
                if (
                    translated_target != ar_translated
                    and not translated_target.startswith(root_prefix)
                ):
                    self._bad_request(
                        "target_subfolder escapes archive root"
                    )
                    return

                # Ensure destination folder hierarchy exists.
                from robotsix_auto_mail.server.adapters import (
                    _ensure_folder_hierarchy,
                )

                _ensure_folder_hierarchy(client, translated_target, delimiter)

                # Select the source folder and perform the move.
                client.select_folder(resolved_source_folder)
                client.move_message(resolved_uid, translated_target)

        except ImapMessageNotFoundError:
            self._not_found()
            return
        except ImapError as exc:
            self._send_response(
                f"IMAP error during archive move: {exc}",
                status=502,
            )
            return
        except OSError as exc:
            self._send_response(
                f"IMAP connection error: {exc}",
                status=502,
            )
            return

        self._serve_json(
            {
                "status": "moved",
                "message_id": message_id or "",
                "uid": resolved_uid,
                "source_folder": resolved_source_folder,
                "target_subfolder": target_subfolder,
            }
        )
