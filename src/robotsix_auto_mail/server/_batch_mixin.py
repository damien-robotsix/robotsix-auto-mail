"""Batch-action mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

from typing import TYPE_CHECKING

from robotsix_auto_mail.core._constants import _BATCH_OP_STATE_KEY
from robotsix_auto_mail.config import DEFAULT_ARCHIVE_ROOT
from robotsix_auto_mail.server.adapters import (
    _batch_op_running,
    _collect_records_for_action,
    _run_batch_archive_background,
    _run_batch_delete_background,
)
from robotsix_auto_mail.triage import TO_ARCHIVE, TO_DELETE


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

        In the aggregate ("All mailboxes") view the request resolves to
        ``self._aggregate`` and is fanned out across every account by
        :meth:`_handle_batch_delete_aggregate`.
        """
        if self._aggregate and self.accounts is not None:
            self._handle_batch_delete_aggregate()
            return

        self._launch_background_worker(
            _BATCH_OP_STATE_KEY,
            _run_batch_delete_background,
            (self.db_path, self.mail_config),
            running_check=_batch_op_running,
            precheck=lambda conn: bool(_collect_records_for_action(conn, TO_DELETE)),
        )

    def _handle_batch_delete_aggregate(self) -> None:
        """Fan out batch-delete across every configured account.

        Each account owns its DB, IMAP connection and ``batch_op:state``
        watermark, so a single-account POST handler cannot span them.  This
        starts one independent background worker per account that has
        ``TO_DELETE`` mail and is not already running a batch op — accounts
        that are busy or have nothing to delete are skipped.  Workers run
        concurrently; each clears its own watermark on completion, and the
        aggregate board banner sums their progress.
        """
        accounts = self.accounts
        if accounts is None:  # pragma: no cover - guarded by the caller
            self._redirect("/board", code=302)
            return

        for account in accounts.accounts:
            db_path = account.config.db_path
            self._launch_background_worker(
                _BATCH_OP_STATE_KEY,
                _run_batch_delete_background,
                (db_path, account.config),
                running_check=_batch_op_running,
                precheck=lambda conn: bool(
                    _collect_records_for_action(conn, TO_DELETE)
                ),
                db_path=db_path,
                redirect=False,
            )

        self._redirect("/board", code=302)

    def _handle_batch_archive_folder(self) -> None:
        """Process POST /batch-archive-folder — archive only the TO_ARCHIVE
        mail whose proposed destination equals the posted ``folder``.

        Reads the relative ``folder`` subfolder from the form body (empty =
        the archive root) and delegates to :meth:`_handle_batch_archive` with
        that filter.  Same single-flight guard, precheck and background worker
        as the column-wide "Archive All", scoped to one destination.
        """
        folder = self._parse_request_body("folder")["folder"]
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
        archive_root = (
            self.mail_config.archive_root
            if self.mail_config is not None
            else DEFAULT_ARCHIVE_ROOT
        )

        self._launch_background_worker(
            _BATCH_OP_STATE_KEY,
            _run_batch_archive_background,
            (self.db_path, self.mail_config, archive_root, subfolder),
            running_check=_batch_op_running,
            precheck=lambda conn: bool(_collect_records_for_action(conn, TO_ARCHIVE)),
        )
