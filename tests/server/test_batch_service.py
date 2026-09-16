"""Unit tests for ``BatchService`` — batch-delete / batch-archive actions.

Drives the service directly against a stub request context.  The context
delegates to the *real* ``launch_background_worker`` helper so ``_launch_background_worker``
(single-flight watermark guard, precheck, daemon spawn) runs — the batch
service only orchestrates it, so the watermark/precheck paths are exercised
end-to-end rather than mocked away.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest import mock

from robotsix_auto_mail.config import (
    DEFAULT_ARCHIVE_ROOT,
    MailAccount,
    MailAccountsConfig,
    MailConfig,
)
from robotsix_auto_mail.core._constants import _BATCH_OP_STATE_KEY
from robotsix_auto_mail.db import init_db, set_watermark
from robotsix_auto_mail.server._batch_service import BatchService
from robotsix_auto_mail.server._request_helpers import launch_background_worker
from tests.server._test_helpers import _SyncThread


class _BatchContext:
    """Stub request context exposing the real ``_launch_background_worker``.

    Delegates ``_launch_background_worker`` to the shared
    :func:`launch_background_worker` helper for the genuine single-flight
    worker launcher, while wiring every response sink to a ``MagicMock`` so
    the service can be exercised without a real HTTP server.
    """

    def _launch_background_worker(self, *args: Any, **kwargs: Any) -> bool:
        return launch_background_worker(self, *args, **kwargs)  # type: ignore[arg-type]

    def __init__(
        self,
        db_path: str,
        mail_config: MailConfig | None = None,
    ) -> None:
        self.db_path: str = db_path
        self.mail_config: MailConfig | None = mail_config
        self.accounts: Any = None
        self._aggregate: bool = False
        self._current_account_id: str | None = None
        self._account_cookie: str | None = None
        self.headers: Any = mock.MagicMock()
        self.rfile: Any = mock.MagicMock()
        self._send_response: Any = mock.MagicMock()
        self._redirect: Any = mock.MagicMock()
        self._not_found: Any = mock.MagicMock()
        self._bad_request: Any = mock.MagicMock()
        self._serve_json: Any = mock.MagicMock()


def _make_service(db_path: str) -> BatchService:
    return BatchService(db_path=db_path)


# ===================================================================
# handle_batch_delete
# ===================================================================


class TestHandleBatchDelete:
    """Unit tests for ``BatchService.handle_batch_delete``."""

    def test_single_account_launches_worker_with_correct_args(
        self, tmp_db_path: str
    ) -> None:
        """When ``_aggregate`` is False the service calls
        ``_launch_background_worker`` with the delete-specific arguments."""
        ctx = _BatchContext(tmp_db_path)
        ctx._aggregate = False
        ctx.accounts = None

        with (
            mock.patch.object(ctx, "_launch_background_worker") as mock_launch,
            mock.patch(
                "robotsix_auto_mail.server._batch_service._run_batch_delete_background"
            ) as mock_delete_bg,
            mock.patch(
                "robotsix_auto_mail.server._batch_service._collect_records_for_action"
            ) as mock_collect,
            mock.patch(
                "robotsix_auto_mail.server._batch_service._batch_op_running"
            ) as mock_running_check,
        ):
            _make_service(tmp_db_path).handle_batch_delete(ctx)

            mock_launch.assert_called_once()
            call_args, call_kwargs = mock_launch.call_args
            # Positional args: watermark_key, target, args
            assert call_args[0] == _BATCH_OP_STATE_KEY
            assert call_args[1] is mock_delete_bg
            assert call_args[2] == (tmp_db_path, ctx.mail_config)
            assert call_kwargs["running_check"] is mock_running_check
            # precheck is a lambda; verify it delegates to
            # _collect_records_for_action (call inside the patch context
            # so the mock is still active)
            precheck = call_kwargs["precheck"]
            assert callable(precheck)
            mock_conn = mock.MagicMock()
            mock_collect.return_value = [mock.MagicMock()]
            assert precheck(mock_conn) is True
            mock_collect.assert_called_once()
            # The first positional arg to _collect_records_for_action is conn
            assert mock_collect.call_args[0][0] is mock_conn

    def test_aggregate_with_accounts_calls_aggregate_handler(
        self, tmp_db_path: str, tmp_path: Path
    ) -> None:
        """When ``_aggregate`` is True and ``accounts`` is not None,
        the service delegates to ``handle_batch_delete_aggregate``."""
        ctx = _BatchContext(tmp_db_path)
        ctx._aggregate = True

        db_a = os.path.join(tmp_path, "a.db")
        db_b = os.path.join(tmp_path, "b.db")
        cfg_a = MailConfig(
            imap_host="imap.a.example.com",
            smtp_host="smtp.a.example.com",
            username="a@example.com",
            password="secret-a",
            db_path=db_a,
            archive_enabled=False,
            triage_on_ingest=False,
        )
        cfg_b = MailConfig(
            imap_host="imap.b.example.com",
            smtp_host="smtp.b.example.com",
            username="b@example.com",
            password="secret-b",
            db_path=db_b,
            archive_enabled=False,
            triage_on_ingest=False,
        )
        ctx.accounts = MailAccountsConfig(
            accounts=[
                MailAccount(account_id="A", config=cfg_a),
                MailAccount(account_id="B", config=cfg_b),
            ],
        )

        service = _make_service(tmp_db_path)
        with mock.patch.object(service, "handle_batch_delete_aggregate") as mock_agg:
            service.handle_batch_delete(ctx)

        mock_agg.assert_called_once_with(ctx)

    def test_running_watermark_prevents_spawn(self, tmp_db_path: str) -> None:
        """When the ``batch_op:state`` watermark is already "running",
        ``_launch_background_worker`` returns False and the worker is
        never started."""
        conn = init_db(tmp_db_path, skip_migrations=True)
        set_watermark(conn, _BATCH_OP_STATE_KEY, "running")
        conn.close()

        ctx = _BatchContext(tmp_db_path)
        ctx._aggregate = False
        ctx.accounts = None

        mock_delete_bg = mock.MagicMock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._batch_service._run_batch_delete_background",
                mock_delete_bg,
            ),
            mock.patch(
                "robotsix_auto_mail.server._request_helpers.threading.Thread",
                _SyncThread,
            ),
        ):
            _make_service(tmp_db_path).handle_batch_delete(ctx)

        # Worker was never spawned because the watermark was running.
        mock_delete_bg.assert_not_called()
        ctx._redirect.assert_called_once_with("/board", code=302)

    def test_precheck_empty_column_skips_worker(self, tmp_db_path: str) -> None:
        """When no records have a ``TO_DELETE`` triage decision the
        precheck returns False and no background worker is spawned."""
        ctx = _BatchContext(tmp_db_path)
        ctx._aggregate = False
        ctx.accounts = None

        mock_delete_bg = mock.MagicMock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._batch_service._run_batch_delete_background",
                mock_delete_bg,
            ),
            mock.patch(
                "robotsix_auto_mail.server._request_helpers.threading.Thread",
                _SyncThread,
            ),
        ):
            _make_service(tmp_db_path).handle_batch_delete(ctx)

        mock_delete_bg.assert_not_called()
        ctx._redirect.assert_called_once_with("/board", code=302)

    def test_precheck_populated_column_spawns_worker(self, tmp_db_path: str) -> None:
        """When at least one record has a ``TO_DELETE`` triage decision
        the precheck passes and the background worker is spawned."""
        from tests.server.conftest_helpers import _populate_db, _seed_triage_decision

        _populate_db(
            tmp_db_path,
            [
                {
                    "message_id": "<del@example.com>",
                    "sender": "s@example.com",
                    "subject": "Delete me",
                    "date": "2025-01-01T00:00:00Z",
                    "body_plain": "body",
                    "status": "unread",
                }
            ],
        )
        _seed_triage_decision(tmp_db_path, "<del@example.com>", action="TO_DELETE")

        ctx = _BatchContext(tmp_db_path)
        ctx._aggregate = False
        ctx.accounts = None

        mock_delete_bg = mock.MagicMock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._batch_service._run_batch_delete_background",
                mock_delete_bg,
            ),
            mock.patch(
                "robotsix_auto_mail.server._request_helpers.threading.Thread",
                _SyncThread,
            ),
        ):
            _make_service(tmp_db_path).handle_batch_delete(ctx)

        mock_delete_bg.assert_called_once_with(tmp_db_path, ctx.mail_config)
        ctx._redirect.assert_called_once_with("/board", code=302)


# ===================================================================
# handle_batch_delete_aggregate
# ===================================================================


class TestHandleBatchDeleteAggregate:
    """Unit tests for ``BatchService.handle_batch_delete_aggregate``."""

    def test_fans_out_launch_worker_per_account(
        self, tmp_db_path: str, tmp_path: Path
    ) -> None:
        """Each configured account gets its own call to
        ``_launch_background_worker`` with ``redirect=False``."""
        db_a = os.path.join(tmp_path, "a.db")
        db_b = os.path.join(tmp_path, "b.db")
        cfg_a = MailConfig(
            imap_host="imap.a.example.com",
            smtp_host="smtp.a.example.com",
            username="a@example.com",
            password="secret-a",
            db_path=db_a,
            archive_enabled=False,
            triage_on_ingest=False,
        )
        cfg_b = MailConfig(
            imap_host="imap.b.example.com",
            smtp_host="smtp.b.example.com",
            username="b@example.com",
            password="secret-b",
            db_path=db_b,
            archive_enabled=False,
            triage_on_ingest=False,
        )

        ctx = _BatchContext(tmp_db_path)
        ctx.accounts = MailAccountsConfig(
            accounts=[
                MailAccount(account_id="A", config=cfg_a),
                MailAccount(account_id="B", config=cfg_b),
            ],
        )

        with mock.patch.object(ctx, "_launch_background_worker") as mock_launch:
            _make_service(tmp_db_path).handle_batch_delete_aggregate(ctx)

        assert mock_launch.call_count == 2

        # First call — account A
        call0_args, call0_kwargs = mock_launch.call_args_list[0]
        assert call0_args[0] == _BATCH_OP_STATE_KEY
        assert call0_kwargs["db_path"] == db_a
        assert call0_kwargs["redirect"] is False

        # Second call — account B
        call1_args, call1_kwargs = mock_launch.call_args_list[1]
        assert call1_args[0] == _BATCH_OP_STATE_KEY
        assert call1_kwargs["db_path"] == db_b
        assert call1_kwargs["redirect"] is False

        ctx._redirect.assert_called_once_with("/board", code=302)

    def test_aggregate_redirects_even_when_all_accounts_busy(
        self, tmp_db_path: str, tmp_path: Path
    ) -> None:
        """The aggregate handler always redirects to /board, even when
        every account's watermark is already running (so no workers
        launch)."""
        db_path = os.path.join(tmp_path, "busy.db")
        cfg = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
            password="secret",
            db_path=db_path,
            archive_enabled=False,
            triage_on_ingest=False,
        )
        ctx = _BatchContext(tmp_db_path)
        ctx.accounts = MailAccountsConfig(
            accounts=[MailAccount(account_id="A", config=cfg)],
        )

        # Don't mock _launch_background_worker — let the real
        # implementation run.  Seed the watermark as "running" on the
        # account's DB so it skips.
        conn = init_db(db_path, skip_migrations=True)
        set_watermark(conn, _BATCH_OP_STATE_KEY, "running")
        conn.close()

        mock_delete_bg = mock.MagicMock()
        with (
            mock.patch(
                "robotsix_auto_mail.server._batch_service._run_batch_delete_background",
                mock_delete_bg,
            ),
            mock.patch(
                "robotsix_auto_mail.server._request_helpers.threading.Thread",
                _SyncThread,
            ),
        ):
            _make_service(tmp_db_path).handle_batch_delete_aggregate(ctx)

        mock_delete_bg.assert_not_called()
        ctx._redirect.assert_called_once_with("/board", code=302)


# ===================================================================
# handle_batch_archive
# ===================================================================


class TestHandleBatchArchive:
    """Unit tests for ``BatchService.handle_batch_archive``."""

    def test_launches_background_worker_with_correct_args(
        self, tmp_db_path: str
    ) -> None:
        """``handle_batch_archive`` calls ``_launch_background_worker`` with
        the archive-specific arguments and the configured archive root."""
        ctx = _BatchContext(tmp_db_path)

        with (
            mock.patch.object(ctx, "_launch_background_worker") as mock_launch,
            mock.patch(
                "robotsix_auto_mail.server._batch_service._run_batch_archive_background"
            ) as mock_archive_bg,
            mock.patch(
                "robotsix_auto_mail.server._batch_service._collect_records_for_action"
            ) as mock_collect,
            mock.patch(
                "robotsix_auto_mail.server._batch_service._batch_op_running"
            ) as mock_running_check,
        ):
            _make_service(tmp_db_path).handle_batch_archive(ctx)

            mock_launch.assert_called_once()
            call_args, call_kwargs = mock_launch.call_args
            assert call_args[0] == _BATCH_OP_STATE_KEY
            assert call_args[1] is mock_archive_bg
            assert call_kwargs["running_check"] is mock_running_check

            # The args tuple should be (db_path, mail_config, archive_root, None)
            args_tuple = call_args[2]
            assert args_tuple[0] == tmp_db_path
            assert args_tuple[1] is ctx.mail_config
            # archive_root comes from mail_config.archive_root or DEFAULT_ARCHIVE_ROOT
            assert args_tuple[3] is None  # subfolder=None for full column

            # precheck delegates to _collect_records_for_action
            precheck = call_kwargs["precheck"]
            assert callable(precheck)
            mock_conn = mock.MagicMock()
            mock_collect.return_value = [mock.MagicMock()]
            assert precheck(mock_conn) is True

    def test_uses_default_archive_root_when_config_is_none(
        self, tmp_db_path: str
    ) -> None:
        """When ``mail_config`` is ``None`` the default archive root
        is used instead."""
        ctx = _BatchContext(tmp_db_path)
        ctx.mail_config = None

        with (
            mock.patch.object(ctx, "_launch_background_worker") as mock_launch,
            mock.patch(
                "robotsix_auto_mail.server._batch_service._run_batch_archive_background"
            ),
        ):
            _make_service(tmp_db_path).handle_batch_archive(ctx)

        args_tuple = mock_launch.call_args[0][2]
        # Third positional arg is archive_root — should be DEFAULT_ARCHIVE_ROOT
        assert args_tuple[2] == DEFAULT_ARCHIVE_ROOT

    def test_with_subfolder_passes_subfolder_in_args(self, tmp_db_path: str) -> None:
        """When called with a *subfolder*, the fourth element of the args
        tuple carries that subfolder value."""
        ctx = _BatchContext(tmp_db_path)

        with (
            mock.patch.object(ctx, "_launch_background_worker") as mock_launch,
            mock.patch(
                "robotsix_auto_mail.server._batch_service._run_batch_archive_background"
            ),
        ):
            _make_service(tmp_db_path).handle_batch_archive(
                ctx, subfolder="Work/Projects"
            )

        args_tuple = mock_launch.call_args[0][2]
        assert args_tuple[3] == "Work/Projects"

    def test_archive_column_guard_prevents_spawn_when_running(
        self, tmp_db_path: str
    ) -> None:
        """When the ``batch_op:state`` watermark is already "running",
        ``handle_batch_archive`` does not spawn the archive worker."""
        conn = init_db(tmp_db_path, skip_migrations=True)
        set_watermark(conn, _BATCH_OP_STATE_KEY, "running")
        conn.close()

        ctx = _BatchContext(tmp_db_path)
        mock_archive_bg = mock.MagicMock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._batch_service._run_batch_archive_background",
                mock_archive_bg,
            ),
            mock.patch(
                "robotsix_auto_mail.server._request_helpers.threading.Thread",
                _SyncThread,
            ),
        ):
            _make_service(tmp_db_path).handle_batch_archive(ctx)

        mock_archive_bg.assert_not_called()
        ctx._redirect.assert_called_once_with("/board", code=302)

    def test_archive_precheck_empty_column_skips_worker(self, tmp_db_path: str) -> None:
        """When no records have a ``TO_ARCHIVE`` triage decision the
        precheck returns False and no background worker is spawned."""
        ctx = _BatchContext(tmp_db_path)
        mock_archive_bg = mock.MagicMock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._batch_service._run_batch_archive_background",
                mock_archive_bg,
            ),
            mock.patch(
                "robotsix_auto_mail.server._request_helpers.threading.Thread",
                _SyncThread,
            ),
        ):
            _make_service(tmp_db_path).handle_batch_archive(ctx)

        mock_archive_bg.assert_not_called()
        ctx._redirect.assert_called_once_with("/board", code=302)


# ===================================================================
# handle_batch_archive_folder
# ===================================================================


class TestHandleBatchArchiveFolder:
    """Unit tests for ``BatchService.handle_batch_archive_folder``."""

    def test_scoped_subfolder_delegates_to_batch_archive(
        self, tmp_db_path: str
    ) -> None:
        """``handle_batch_archive_folder`` reads the ``folder`` field from
        the form body and delegates to ``handle_batch_archive`` with that
        subfolder."""
        ctx = _BatchContext(tmp_db_path)
        ctx.headers = mock.MagicMock()
        ctx.headers.get.return_value = "0"
        ctx.rfile = mock.MagicMock()
        ctx.rfile.read.return_value = b"folder=Receipts%2F2025"

        service = _make_service(tmp_db_path)
        with mock.patch.object(service, "handle_batch_archive") as mock_archive:
            service.handle_batch_archive_folder(ctx)

        mock_archive.assert_called_once_with(ctx, subfolder="Receipts/2025")

    def test_empty_folder_passes_empty_subfolder(self, tmp_db_path: str) -> None:
        """An empty ``folder`` field delegates to ``handle_batch_archive``
        with ``subfolder=""`` (archive the whole column)."""
        ctx = _BatchContext(tmp_db_path)
        ctx.headers = mock.MagicMock()
        ctx.headers.get.return_value = "0"
        ctx.rfile = mock.MagicMock()
        ctx.rfile.read.return_value = b"folder="

        service = _make_service(tmp_db_path)
        with mock.patch.object(service, "handle_batch_archive") as mock_archive:
            service.handle_batch_archive_folder(ctx)

        mock_archive.assert_called_once_with(ctx, subfolder="")
