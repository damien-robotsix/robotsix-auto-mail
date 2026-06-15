"""Action-handler mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

from robotsix_auto_mail.config import DEFAULT_ARCHIVE_ROOT, MailConfig
from robotsix_auto_mail.db import MailRecord
from robotsix_auto_mail.server._constants import _is_safe_redirect_path
from robotsix_auto_mail.triage import (
    VALID_TRIAGE_ACTIONS,
    get_archive_subfolder,
    propose_archive_subfolder_llm,
    record_archive_folder_choice,
    record_human_decision,
    set_triage_decision,
)


class _BoardActionMixin:
    """Mixin providing POST action handlers for the board server."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    def _parse_request_body(
        self, *fields: str, no_strip: frozenset[str] = frozenset()
    ) -> dict[str, str]:
        """Parse the request body as URL-encoded form data.

        Returns a dict mapping each requested *field* name to its
        first value.  Values are stripped of leading/trailing
        whitespace *unless* the field name appears in *no_strip*.
        """
        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        parsed = parse_qs(raw)
        return {
            field: (
                (parsed.get(field) or [""])[0].strip()
                if field not in no_strip
                else (parsed.get(field) or [""])[0]
            )
            for field in fields
        }

    def _handle_move(self) -> None:
        """Process POST /move — update a card's triage decision and redirect."""
        from robotsix_auto_mail.db import get_record_by_message_id, init_db

        f = self._parse_request_body("message_id", "triage_action", "redirect_to")
        message_id = f["message_id"]
        triage_action = f["triage_action"]
        redirect_to = f["redirect_to"]

        if not message_id or not triage_action:
            self._bad_request("Missing message_id or triage_action")
            return

        if triage_action not in VALID_TRIAGE_ACTIONS:
            self._bad_request(f"Invalid triage action: {triage_action!r}")
            return

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            # Verify the record exists before upserting a triage decision
            # (foreign key would reject it anyway, but we want a clean 404).
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._not_found()
                return
            set_triage_decision(
                conn,
                message_id,
                triage_action,
                source="user",
                reason=f"moved to {triage_action}",
            )
            record_human_decision(conn, message_id, triage_action)

            if triage_action == "TO_ARCHIVE":
                try:
                    if record is not None and self.mail_config is not None:
                        propose_archive_subfolder_llm(
                            conn,
                            record,
                            self.mail_config.llm_api_key,
                            provider=(
                                self.mail_config.llm_provider
                                if self.mail_config
                                else None
                            ),
                        )
                except Exception:  # noqa: S110  # nosec B110
                    pass  # Non-fatal: board falls back to deterministic proposal
        finally:
            conn.close()

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
            self._redirect("/board", code=302)

    def _handle_delete(self) -> None:
        """Process POST /delete — delete mail from IMAP mailbox and local DB."""
        from robotsix_auto_mail.db import (
            delete_record_by_message_id,
            get_record_by_message_id,
            init_db,
        )

        f = self._parse_request_body("message_id", "redirect_to")
        message_id = f["message_id"]
        redirect_to = f["redirect_to"]

        if not message_id:
            self._bad_request("Missing message_id")
            return

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._not_found()
                return

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
                    from robotsix_auto_mail.db import update_record_source
                    from robotsix_auto_mail.imap import cross_folder_resolve

                    try:
                        with ImapClient(self.mail_config) as client2:
                            cross = cross_folder_resolve(client2, record.message_id)
                            if cross is not None:
                                new_folder, new_uid = cross
                                update_record_source(
                                    conn,
                                    message_id,
                                    source_folder=new_folder,
                                    imap_uid=new_uid,
                                )
                                client2.delete_message(new_uid)
                    except (ImapError, OSError) as exc:
                        self._send_response(
                            f"IMAP cross-folder resolution failed: {exc}",
                            status=502,
                        )
                        return
                except (ImapError, OSError) as exc:
                    self._send_response(
                        f"IMAP deletion failed: {exc}",
                        status=502,
                    )
                    return

            # -- local DB deletion --
            delete_record_by_message_id(conn, message_id)
        finally:
            conn.close()

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
            self._redirect("/board", code=302)

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
            parts = dest_folder.split(delimiter)
            for i in range(1, len(parts) + 1):
                client.create_folder(delimiter.join(parts[:i]))

            client.move_message(resolved_uid, dest_folder)

    def _archive_and_delete(self, conn: Any, record: MailRecord) -> bool:
        """Archive *record*'s message via IMAP, then delete its local row.

        Shared by :meth:`_handle_archive` and :meth:`_handle_send_draft`.
        Computes the effective archive root + subfolder, performs the IMAP
        move (only when IMAP is configured and the record has a tracked
        UID), then removes the local database record.

        Returns ``True`` on success.  On a security-policy violation it
        sends a 400 and returns ``False``; on an IMAP/IO failure it sends a
        502 and returns ``False`` — in both error cases the local record is
        left intact.
        """
        from robotsix_auto_mail.db import delete_record_by_message_id

        # Compute the effective archive subfolder.
        subfolder = get_archive_subfolder(
            conn,
            record.message_id,
            record,
            api_key=self.mail_config.llm_api_key if self.mail_config else "",
        )

        # Determine the archive root.
        archive_root = (
            self.mail_config.archive_root
            if self.mail_config is not None
            else DEFAULT_ARCHIVE_ROOT
        )

        # Determine the namespace prefix (empty when unset).
        namespace = (
            self.mail_config.archive_namespace if self.mail_config is not None else ""
        )

        # Effective root: namespace + archive_root (user supplies
        # the delimiter as part of the namespace, e.g. "INBOX.").
        effective_root = namespace + archive_root

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
            except ValueError as exc:
                self._bad_request(str(exc))
                return False
            except ImapMessageNotFoundError:
                from robotsix_auto_mail.db import (
                    delete_record_by_message_id,
                    update_record_source,
                )
                from robotsix_auto_mail.imap import (
                    ImapClient,
                    cross_folder_resolve,
                )
                from robotsix_auto_mail.server.adapters import _archive_dest_folder

                try:
                    with ImapClient(self.mail_config) as client2:
                        cross = cross_folder_resolve(client2, record.message_id)
                        if cross is not None:
                            new_folder, new_uid = cross
                            update_record_source(
                                conn,
                                record.message_id,
                                source_folder=new_folder,
                                imap_uid=new_uid,
                            )
                            # Compute the archive destination.
                            delimiter = next(
                                (
                                    f.delimiter
                                    for f in client2.list_folders()
                                    if f.delimiter
                                ),
                                "/",
                            )
                            dest_folder = _archive_dest_folder(
                                effective_root, subfolder, delimiter
                            )
                            if dest_folder is None:
                                raise ValueError(
                                    "Archive destination escapes archive root"
                                )
                            # Ensure destination hierarchy exists.
                            parts = dest_folder.split(delimiter)
                            for i in range(1, len(parts) + 1):
                                client2.create_folder(delimiter.join(parts[:i]))
                            client2.move_message(new_uid, dest_folder)
                        # Mail gone or healed — delete the local record
                        # in both cases.
                        delete_record_by_message_id(conn, record.message_id)
                        return True
                except ValueError as exc:
                    self._bad_request(str(exc))
                    return False
                except (ImapError, OSError) as exc:
                    self._send_response(
                        f"IMAP cross-folder resolution failed: {exc}",
                        status=502,
                    )
                    return False
            except (ImapError, OSError) as exc:
                self._send_response(
                    f"IMAP archive failed: {exc}",
                    status=502,
                )
                return False

        # -- record the human-confirmed archive-folder choice (best-effort),
        #    BEFORE the local row is deleted so the memory survives --
        if subfolder:
            try:
                record_archive_folder_choice(conn, record, subfolder)
            except Exception:  # noqa: S110  # nosec B110
                pass  # Non-fatal: memory is advisory only

        # -- local DB cleanup --
        delete_record_by_message_id(conn, record.message_id)
        return True

    def _handle_archive(self) -> None:
        """Process POST /archive — move mail to archive folder via IMAP
        and remove it from the local database.
        """
        from robotsix_auto_mail.db import get_record_by_message_id, init_db

        f = self._parse_request_body("message_id", "redirect_to")
        message_id = f["message_id"]
        redirect_to = f["redirect_to"]

        if not message_id:
            self._bad_request("Missing message_id")
            return

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._not_found()
                return

            if not self._archive_and_delete(conn, record):
                return
        finally:
            conn.close()

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
            self._redirect("/board", code=302)

    def _handle_save_notes(self) -> None:
        """Process POST /save-notes — persist notes for a mail record."""
        from robotsix_auto_mail.db import (
            get_record_by_message_id,
            init_db,
            update_notes,
        )

        f = self._parse_request_body(
            "message_id", "redirect_to", "notes", no_strip=frozenset({"notes"})
        )
        message_id = f["message_id"]
        redirect_to = f["redirect_to"]
        notes = f["notes"]

        if not message_id:
            self._bad_request("Missing message_id")
            return

        # Verify the record exists (read-only check).
        conn = init_db(self.db_path, skip_migrations=True)
        try:
            if get_record_by_message_id(conn, message_id) is None:
                self._not_found()
                return
        finally:
            conn.close()

        # Persist the notes.
        conn = init_db(self.db_path)
        try:
            update_notes(conn, message_id, notes)
        finally:
            conn.close()

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
            self._redirect("/board", code=302)
