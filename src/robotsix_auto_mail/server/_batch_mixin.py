"""Batch-action mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

from typing import TYPE_CHECKING

from robotsix_auto_mail.config import DEFAULT_ARCHIVE_ROOT
from robotsix_auto_mail.server.adapters import (
    _batch_op_running,
    _collect_records_for_action,
    _run_batch_archive_background,
    _run_batch_delete_background,
)


class _BatchActionMixin:
    """Mixin providing batch-delete and batch-archive actions."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    def _handle_batch_delete(self) -> None:
        """Process POST /batch-delete — delete all TO_DELETE mail from IMAP
        and local DB in a background daemon thread.

        Returns the redirect **immediately** with no synchronous IMAP work,
        so the browser is never held while a large column is processed.
        Single-flight guarded by the shared ``batch_op:state`` watermark (so
        delete and archive cannot run concurrently on the same account); the
        daemon worker does all IMAP + DB deletion and heals any stale UIDs
        itself (``resolve_uid_with_fallback`` / ``cross_folder_resolve``).
        Progress and completion show via the board's batch banner and the
        30-second auto-refresh.
        """
        import threading

        from robotsix_auto_mail.db import get_watermark, init_db, set_watermark

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            if _batch_op_running(get_watermark(conn, "batch_op:state")):
                self._redirect("/board", code=302)
                return
            # Nothing to do → don't spawn a no-op worker / set "running".
            if not _collect_records_for_action(conn, "TO_DELETE"):
                self._redirect("/board", code=302)
                return
            set_watermark(conn, "batch_op:state", "running")
        finally:
            conn.close()

        threading.Thread(
            target=_run_batch_delete_background,
            args=(self.db_path, self.mail_config),
            daemon=True,
        ).start()

        self._redirect("/board", code=302)

    def _handle_batch_archive_folder(self) -> None:
        """Process POST /batch-archive-folder — archive only the TO_ARCHIVE
        mail whose proposed destination equals the posted ``folder``.

        Reads the relative ``folder`` subfolder from the form body (empty =
        the archive root) and delegates to :meth:`_handle_batch_archive` with
        that filter.  Same single-flight guard, precheck and background worker
        as the column-wide "Archive All", scoped to one destination.
        """
        from urllib.parse import parse_qs

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)
        folder = (fields.get("folder") or [""])[0].strip()
        self._handle_batch_archive(subfolder=folder)

    def _handle_batch_archive(self, subfolder: str | None = None) -> None:
        """Process POST /batch-archive — archive all TO_ARCHIVE mail from
        IMAP and local DB in a background daemon thread.

        Returns the redirect **immediately** with no synchronous IMAP work,
        so the browser is never held while a large column is processed.
        When *subfolder* is not ``None`` only that destination's mail is
        archived (see :meth:`_handle_batch_archive_folder`); ``None`` archives
        the whole column.  Single-flight guarded by the shared
        ``batch_op:state`` watermark (so delete and archive cannot run
        concurrently on the same account); the daemon worker groups UIDs by
        destination, heals stale UIDs itself, and batch-moves each group.
        Progress shows via the board's batch banner and 30-second refresh.
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
            # Nothing to do → don't spawn a no-op worker / set "running".
            if not _collect_records_for_action(conn, "TO_ARCHIVE"):
                self._redirect("/board", code=302)
                return
            set_watermark(conn, "batch_op:state", "running")
        finally:
            conn.close()

        threading.Thread(
            target=_run_batch_archive_background,
            args=(self.db_path, self.mail_config, archive_root, subfolder),
            daemon=True,
        ).start()

        self._redirect("/board", code=302)
