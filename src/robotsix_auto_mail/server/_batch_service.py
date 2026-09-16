"""Batch-action service for the board server."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from robotsix_auto_mail.config import DEFAULT_ARCHIVE_ROOT
from robotsix_auto_mail.core._constants import _BATCH_OP_STATE_KEY
from robotsix_auto_mail.server._request_helpers import parse_request_body
from robotsix_auto_mail.server._services import Service
from robotsix_auto_mail.server.adapters import (
    _batch_op_running,
    _collect_records_for_action,
    _run_batch_archive_background,
    _run_batch_delete_background,
)
from robotsix_auto_mail.triage import TO_ARCHIVE, TO_DELETE

if TYPE_CHECKING:
    from robotsix_auto_mail.config import MailAccountsConfig, MailConfig
    from robotsix_auto_mail.server._board_handler_protocol import RequestContext


class BatchService(Service):
    """Stateless service providing batch-delete and batch-archive actions."""

    def handle_batch_delete(self, ctx: RequestContext) -> None:
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
        ``ctx._aggregate`` and is fanned out across every account by
        :meth:`handle_batch_delete_aggregate`.
        """
        if ctx._aggregate and ctx.accounts is not None:
            self.handle_batch_delete_aggregate(ctx)
            return

        ctx._launch_background_worker(
            _BATCH_OP_STATE_KEY,
            _run_batch_delete_background,
            (ctx.db_path, ctx.mail_config),
            running_check=_batch_op_running,
            precheck=lambda conn: bool(_collect_records_for_action(conn, TO_DELETE)),
        )

    def handle_batch_delete_aggregate(self, ctx: RequestContext) -> None:
        """Fan out batch-delete across every configured account.

        Each account owns its DB, IMAP connection and ``batch_op:state``
        watermark, so a single-account POST handler cannot span them.  This
        starts one independent background worker per account that has
        ``TO_DELETE`` mail and is not already running a batch op — accounts
        that are busy or have nothing to delete are skipped.  Workers run
        concurrently; each clears its own watermark on completion, and the
        aggregate board banner sums their progress.
        """
        accounts = cast("MailAccountsConfig | None", ctx.accounts)
        if accounts is None:  # pragma: no cover - guarded by the caller
            ctx._redirect("/board", code=302)
            return

        for account in accounts.accounts:
            db_path = account.config.db_path
            ctx._launch_background_worker(
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

        ctx._redirect("/board", code=302)

    def handle_batch_archive_folder(self, ctx: RequestContext) -> None:
        """Process POST /batch-archive-folder — archive only the TO_ARCHIVE
        mail whose proposed destination equals the posted ``folder``.

        Reads the relative ``folder`` subfolder from the form body (empty =
        the archive root) and delegates to :meth:`handle_batch_archive` with
        that filter.  Same single-flight guard, precheck and background worker
        as the column-wide "Archive All", scoped to one destination.
        """
        folder = parse_request_body(ctx, "folder")["folder"]
        self.handle_batch_archive(ctx, subfolder=folder)

    def handle_batch_archive(
        self, ctx: RequestContext, subfolder: str | None = None
    ) -> None:
        """Process POST /batch-archive — archive all TO_ARCHIVE mail from
        IMAP and local DB in a background daemon thread.

        Returns the redirect **immediately** with no synchronous IMAP work,
        so the browser is never held while a large column is processed.
        When *subfolder* is not ``None`` only that destination's mail is
        archived (see :meth:`handle_batch_archive_folder`); ``None`` archives
        the whole column.  Single-flight guarded by the shared
        ``batch_op:state`` watermark (so delete and archive cannot run
        concurrently on the same account); the daemon worker groups UIDs by
        destination, heals stale UIDs itself, and batch-moves each group.
        Progress shows via the board's batch banner and 30-second refresh.
        """
        mail_config = cast("MailConfig | None", ctx.mail_config)
        archive_root = (
            mail_config.archive_root
            if mail_config is not None
            else DEFAULT_ARCHIVE_ROOT
        )

        ctx._launch_background_worker(
            _BATCH_OP_STATE_KEY,
            _run_batch_archive_background,
            (ctx.db_path, ctx.mail_config, archive_root, subfolder),
            running_check=_batch_op_running,
            precheck=lambda conn: bool(_collect_records_for_action(conn, TO_ARCHIVE)),
        )
