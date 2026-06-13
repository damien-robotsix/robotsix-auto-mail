"""Batch-action mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

from typing import TYPE_CHECKING

from robotsix_auto_mail.config import DEFAULT_ARCHIVE_ROOT
from robotsix_auto_mail.server.adapters import (
    _batch_op_running,
    _collect_records_for_action,
    _release_batch_op,
    _run_batch_archive_background,
    _run_batch_delete_background,
)


class _BatchActionMixin:
    """Mixin providing batch-delete and batch-archive actions."""

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

    def _handle_batch_delete(self) -> None:
        """Process POST /batch-delete — delete all TO_DELETE mail from IMAP
        and local DB in a background daemon thread.

        Single-flight guarded by the shared ``batch_op:state`` watermark
        (so delete and archive cannot run concurrently on the same
        account).  Before handing off to the background worker, a
        **synchronous stale-UID precheck** verifies that every tracked
        UID still exists in the selected IMAP folder.  If any UID is
        stale the handler responds with **409** and nothing is deleted
        — mirroring the single-delete path in :meth:`_handle_delete`.
        """
        import threading

        from robotsix_auto_mail.db import get_watermark, init_db, set_watermark

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            if _batch_op_running(get_watermark(conn, "batch_op:state")):
                self._redirect("/board", code=302)
                return
            set_watermark(conn, "batch_op:state", "running")
        finally:
            conn.close()

        # -- synchronous stale-UID precheck (before redirect) --
        if self.mail_config is not None:
            from robotsix_auto_mail.imap import (
                ImapClient,
                ImapError,
                ImapMessageNotFoundError,
                resolve_uid_with_fallback,
            )

            conn = init_db(self.db_path, skip_migrations=True)
            try:
                records = _collect_records_for_action(conn, "TO_DELETE")
            finally:
                conn.close()

            if any(r.imap_uid is not None for r in records):
                try:
                    with ImapClient(self.mail_config) as client:
                        for record in records:
                            if record.imap_uid is None:
                                continue
                            resolve_uid_with_fallback(
                                client,
                                record.source_folder,
                                record.imap_uid,
                                record.message_id,
                            )
                except ImapMessageNotFoundError as exc:
                    _release_batch_op(self.db_path)
                    self._send_response(
                        f"Batch delete aborted — a tracked UID is stale, "
                        f"so no messages were deleted: {exc}",
                        status=409,
                    )
                    return
                except (ImapError, OSError) as exc:
                    _release_batch_op(self.db_path)
                    self._send_response(
                        f"IMAP precheck failed: {exc}",
                        status=502,
                    )
                    return

        threading.Thread(
            target=_run_batch_delete_background,
            args=(self.db_path, self.mail_config),
            daemon=True,
        ).start()

        self._redirect("/board", code=302)

    def _handle_batch_archive(self) -> None:
        """Process POST /batch-archive — archive all TO_ARCHIVE mail from
        IMAP and local DB in a background daemon thread.

        Single-flight guarded by the shared ``batch_op:state`` watermark
        (so delete and archive cannot run concurrently on the same
        account).  Before handing off to the background worker, a
        **synchronous stale-UID precheck** verifies that every tracked
        UID still exists in the selected IMAP folder.  If any UID is
        stale the handler responds with **409** and nothing is archived
        — mirroring the single-archive path.  The redirect is returned
        after the precheck passes; the worker groups UIDs by destination
        folder and batch-moves each group.
        """
        import threading

        from robotsix_auto_mail.db import get_watermark, init_db, set_watermark

        archive_root = (
            self.mail_config.archive_root
            if self.mail_config is not None
            else DEFAULT_ARCHIVE_ROOT
        )

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            if _batch_op_running(get_watermark(conn, "batch_op:state")):
                self._redirect("/board", code=302)
                return
            set_watermark(conn, "batch_op:state", "running")
        finally:
            conn.close()

        # -- synchronous stale-UID precheck (before redirect) --
        if self.mail_config is not None:
            from robotsix_auto_mail.imap import (
                ImapClient,
                ImapError,
                ImapMessageNotFoundError,
                resolve_uid_with_fallback,
            )

            conn = init_db(self.db_path, skip_migrations=True)
            try:
                records = _collect_records_for_action(conn, "TO_ARCHIVE")
            finally:
                conn.close()

            if any(r.imap_uid is not None for r in records):
                try:
                    with ImapClient(self.mail_config) as client:
                        for record in records:
                            if record.imap_uid is None:
                                continue
                            resolve_uid_with_fallback(
                                client,
                                record.source_folder,
                                record.imap_uid,
                                record.message_id,
                            )
                except ImapMessageNotFoundError as exc:
                    _release_batch_op(self.db_path)
                    self._send_response(
                        f"Batch archive aborted — a tracked UID is stale, "
                        f"so no messages were archived: {exc}",
                        status=409,
                    )
                    return
                except (ImapError, OSError) as exc:
                    _release_batch_op(self.db_path)
                    self._send_response(
                        f"IMAP precheck failed: {exc}",
                        status=502,
                    )
                    return

        threading.Thread(
            target=_run_batch_archive_background,
            args=(self.db_path, self.mail_config, archive_root),
            daemon=True,
        ).start()

        self._redirect("/board", code=302)
