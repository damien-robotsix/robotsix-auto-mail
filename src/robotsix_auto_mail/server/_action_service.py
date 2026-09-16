"""POST action service for the board server.

Hosts the board's card-mutation endpoints — ``POST /move``, ``POST /delete``
and ``POST /save-notes`` — as a stateless :class:`._services.Service`.  Each
handler takes the per-request context (:class:`RequestContext`) and delegates
the shared parse → look-up → redirect skeleton to
:func:`robotsix_auto_mail.server._request_helpers.handle_post_action`.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Any, cast

from robotsix_auto_mail.config import (
    APP_CLASSIFIER,
    resolve_llm_api_key,
    resolve_llm_tier,
)
from robotsix_auto_mail.db import MailRecord
from robotsix_auto_mail.server._request_helpers import handle_post_action
from robotsix_auto_mail.server._services import Service
from robotsix_auto_mail.triage import (
    TO_ARCHIVE,
    TO_CALENDAR,
    VALID_TRIAGE_ACTIONS,
    propose_archive_subfolder_llm,
    set_triage_decision,
)

if TYPE_CHECKING:
    from robotsix_auto_mail.config import MailConfig
    from robotsix_auto_mail.server._board_handler_protocol import RequestContext

logger = logging.getLogger(__name__)


class ActionService(Service):
    """Stateless service providing the board's POST action handlers."""

    def handle_move(self, ctx: RequestContext) -> None:
        """Process POST /move — update a card's triage decision and redirect."""

        def move_action(
            conn: Any, record: MailRecord, redirect_to: str, triage_action: str
        ) -> bool:
            if not triage_action:
                ctx._bad_request("Missing triage_action")
                return False
            if triage_action not in VALID_TRIAGE_ACTIONS:
                ctx._bad_request(f"Invalid triage action: {triage_action!r}")
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
                ctx._bad_request(f"Could not move to {triage_action}")
                return False

            mail_config = cast("MailConfig | None", ctx.mail_config)
            if triage_action == TO_ARCHIVE:
                try:
                    if mail_config is not None:
                        classifier_level, classifier_model = resolve_llm_tier(
                            APP_CLASSIFIER
                        )
                        propose_archive_subfolder_llm(
                            conn,
                            record,
                            resolve_llm_api_key(raise_on_missing=False),
                            provider_model=classifier_model or None,
                            level=classifier_level,
                            rules=mail_config.triage_guidance,
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

        handle_post_action(
            ctx,
            "message_id",
            "triage_action",
            "redirect_to",
            action=move_action,
        )

    def handle_delete(self, ctx: RequestContext) -> None:
        """Process POST /delete — delete mail from IMAP mailbox and local DB."""
        from robotsix_auto_mail.db import delete_record_by_message_id

        def delete_action(conn: Any, record: MailRecord, redirect_to: str) -> bool:
            mail_config = cast("MailConfig | None", ctx.mail_config)
            # -- IMAP deletion (when config and UID are both available) --
            if mail_config is not None and record.imap_uid is not None:
                from robotsix_auto_mail.imap import (
                    ImapClient,
                    ImapError,
                    ImapMessageNotFoundError,
                    resolve_uid_with_fallback,
                )

                try:
                    with ImapClient(mail_config) as client:
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
                        result = _imap_cross_folder_fallback(mail_config, record, conn)
                    except (ImapError, OSError) as exc:
                        ctx._send_response(
                            f"IMAP cross-folder resolution failed: {exc}",
                            status=502,
                        )
                        return False
                    if result is not None:
                        new_folder, new_uid = result
                        try:
                            with ImapClient(mail_config) as client2:
                                client2.select_folder(new_folder)
                                client2.delete_message(new_uid)
                        except (ImapError, OSError) as exc:
                            ctx._send_response(
                                f"IMAP cross-folder resolution failed: {exc}",
                                status=502,
                            )
                            return False
                except (ImapError, OSError) as exc:
                    ctx._send_response(
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
                mail_config is not None
                and record.imap_uid is None
                and record.message_id.startswith("<compose-")
            ):
                from robotsix_auto_mail.imap import ImapClient, ImapError

                try:
                    with ImapClient(mail_config) as client:
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

        handle_post_action(
            ctx,
            "message_id",
            "redirect_to",
            action=delete_action,
            cross_account=True,
        )

    def handle_save_notes(self, ctx: RequestContext) -> None:
        """Process POST /save-notes — persist notes for a mail record."""
        from robotsix_auto_mail.db import update_notes

        def save_notes_action(
            conn: Any, record: MailRecord, redirect_to: str, notes: str
        ) -> bool:
            update_notes(conn, record.message_id, notes)
            return True

        handle_post_action(
            ctx,
            "message_id",
            "redirect_to",
            "notes",
            no_strip=frozenset({"notes"}),
            action=save_notes_action,
        )
