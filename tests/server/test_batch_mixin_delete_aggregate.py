"""Unit tests for ``_handle_batch_delete_aggregate`` (fan-out)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.core._constants import _BATCH_OP_STATE_KEY
from robotsix_auto_mail.db import init_db, set_watermark
from tests.server._test_helpers import _BatchFakeHandler, _SyncThread


class TestHandleBatchDeleteAggregate:
    """Unit tests for ``_handle_batch_delete_aggregate``."""

    def test_fans_out_launch_worker_per_account(
        self, tmp_db_path: str, tmp_path: Path
    ) -> None:
        """Each configured account gets its own call to
        ``_launch_background_worker`` with ``redirect=False``."""
        import os

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

        handler = _BatchFakeHandler(tmp_db_path)
        handler.accounts = MailAccountsConfig(
            accounts=[
                MailAccount(account_id="A", config=cfg_a),
                MailAccount(account_id="B", config=cfg_b),
            ],
            default_account_id="A",
        )

        with mock.patch.object(handler, "_launch_background_worker") as mock_launch:
            handler._handle_batch_delete_aggregate()

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

        handler._redirect.assert_called_once_with("/board", code=302)

    def test_aggregate_redirects_even_when_all_accounts_busy(
        self, tmp_db_path: str, tmp_path: Path
    ) -> None:
        """The aggregate handler always redirects to /board, even when
        every account's watermark is already running (so no workers
        launch)."""
        import os

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
        handler = _BatchFakeHandler(tmp_db_path)
        handler.accounts = MailAccountsConfig(
            accounts=[MailAccount(account_id="A", config=cfg)],
            default_account_id="A",
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
                "robotsix_auto_mail.server._batch_mixin._run_batch_delete_background",
                mock_delete_bg,
            ),
            mock.patch(
                "robotsix_auto_mail.server._action_mixin.threading.Thread",
                _SyncThread,
            ),
        ):
            handler._handle_batch_delete_aggregate()

        mock_delete_bg.assert_not_called()
        handler._redirect.assert_called_once_with("/board", code=302)
