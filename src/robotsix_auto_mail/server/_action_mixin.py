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

    # -- mypy: declare attributes and methods provided by BoardHandler ---
    if TYPE_CHECKING:
        from collections.abc import Mapping as _Mapping

        from robotsix_auto_mail.config import (
            MailAccountsConfig as _MailAccountsConfig,
        )
        from robotsix_auto_mail.config import (
            MailConfig as _MailConfig,
        )

        db_path: str
        mail_config: _MailConfig | None
        accounts: _MailAccountsConfig | None
        _current_account_id: str | None
        _aggregate: bool
        _account_cookie: str | None
        default_account_id: str | None

        def _send_response(
            self,
            body: bytes | str,
            status: int = 200,
            content_type: str = "text/plain; charset=utf-8",
        ) -> None: ...
        def _redirect(self, location: str, code: int = 301) -> None: ...
        def _not_found(self) -> None: ...
        def _bad_request(self, message: str) -> None: ...
        def _serve_json(
            self, payload: _Mapping[str, object], status: int = 200
        ) -> None: ...

    def _handle_move(self) -> None:
        """Process POST /move — update a card's triage decision and redirect."""
        from robotsix_auto_mail.db import get_record_by_message_id, init_db

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        # parse_qs returns {key: [value, ...]} — extract first value.
        message_id = (fields.get("message_id") or [""])[0].strip()
        triage_action = (fields.get("triage_action") or [""])[0].strip()
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

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

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

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
                except ImapMessageNotFoundError as exc:
                    folder_label = record.source_folder or "its source folder"
                    self._send_response(
                        f"Message {message_id} is no longer in "
                        f"{folder_label} — the tracked UID is stale, "
                        f"so it was not deleted and the board record "
                        f"was kept: {exc}",
                        status=409,
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

            # Build the destination IMAP folder name.
            if subfolder:
                translated = subfolder.replace("/", delimiter)
                dest_folder = f"{effective_root}{delimiter}{translated}"
            else:
                dest_folder = effective_root

            # -- security gate ---------------------------------
            # Reject any destination that escapes the archive
            # root (must start with root+delimiter or equal the
            # root itself) and forbid ".." path segments.
            root_prefix = f"{effective_root}{delimiter}"
            if dest_folder != effective_root and not dest_folder.startswith(
                root_prefix
            ):
                raise ValueError("Archive destination escapes archive root")
            if ".." in dest_folder.split(delimiter):
                raise ValueError("Archive destination contains '..' path segment")

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
            user_email=self.mail_config.username if self.mail_config else None,
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
            except ImapMessageNotFoundError as exc:
                folder_label = record.source_folder or "its source folder"
                self._send_response(
                    f"Message {record.message_id} is no longer in "
                    f"{folder_label} — the tracked UID is stale, so "
                    f"it was not archived and the board record was "
                    f"kept: {exc}",
                    status=409,
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

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()

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

        self._redirect("/board", code=302)

    def _handle_save_notes(self) -> None:
        """Process POST /save-notes — persist notes for a mail record."""
        from robotsix_auto_mail.db import (
            get_record_by_message_id,
            init_db,
            update_notes,
        )

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()
        notes = (fields.get("notes") or [""])[0]
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

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
