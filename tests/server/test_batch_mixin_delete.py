"""Unit tests for ``_handle_batch_delete`` (single-flight guard, precheck)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.core._constants import _BATCH_OP_STATE_KEY
from robotsix_auto_mail.db import init_db, set_watermark
from tests.server._test_helpers import _BatchFakeHandler, _SyncThread


class TestHandleBatchDelete:
    """Unit tests for ``_handle_batch_delete``."""

    def test_single_account_launches_worker_with_correct_args(
        self, tmp_db_path: str
    ) -> None:
        """When ``_aggregate`` is False the method calls
        ``_launch_background_worker`` with the delete-specific arguments."""
        handler = _BatchFakeHandler(tmp_db_path)
        handler._aggregate = False
        handler.accounts = None

        with (
            mock.patch.object(handler, "_launch_background_worker") as mock_launch,
            mock.patch(
                "robotsix_auto_mail.server._batch_mixin._run_batch_delete_background"
            ) as mock_delete_bg,
            mock.patch(
                "robotsix_auto_mail.server._batch_mixin._collect_records_for_action"
            ) as mock_collect,
            mock.patch(
                "robotsix_auto_mail.server._batch_mixin._batch_op_running"
            ) as mock_running_check,
        ):
            handler._handle_batch_delete()

            mock_launch.assert_called_once()
            call_args, call_kwargs = mock_launch.call_args
            # Positional args: watermark_key, target, args
            assert call_args[0] == _BATCH_OP_STATE_KEY
            assert call_args[1] is mock_delete_bg
            assert call_args[2] == (tmp_db_path, handler.mail_config)
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
        the method delegates to ``_handle_batch_delete_aggregate``."""
        handler = _BatchFakeHandler(tmp_db_path)
        handler._aggregate = True

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
        handler.accounts = MailAccountsConfig(
            accounts=[
                MailAccount(account_id="A", config=cfg_a),
                MailAccount(account_id="B", config=cfg_b),
            ],
            default_account_id="A",
        )

        with mock.patch.object(
            handler, "_handle_batch_delete_aggregate"
        ) as mock_aggregate:
            handler._handle_batch_delete()

        mock_aggregate.assert_called_once()

    def test_running_watermark_prevents_spawn(self, tmp_db_path: str) -> None:
        """When the ``batch_op:state`` watermark is already "running",
        ``_launch_background_worker`` returns False and the worker is
        never started."""
        conn = init_db(tmp_db_path, skip_migrations=True)
        set_watermark(conn, _BATCH_OP_STATE_KEY, "running")
        conn.close()

        handler = _BatchFakeHandler(tmp_db_path)
        handler._aggregate = False
        handler.accounts = None

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
            handler._handle_batch_delete()

        # Worker was never spawned because the watermark was running.
        mock_delete_bg.assert_not_called()
        handler._redirect.assert_called_once_with("/board", code=302)

    def test_precheck_empty_column_skips_worker(self, tmp_db_path: str) -> None:
        """When no records have a ``TO_DELETE`` triage decision the
        precheck returns False and no background worker is spawned."""
        handler = _BatchFakeHandler(tmp_db_path)
        handler._aggregate = False
        handler.accounts = None

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
            handler._handle_batch_delete()

        mock_delete_bg.assert_not_called()
        handler._redirect.assert_called_once_with("/board", code=302)

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

        handler = _BatchFakeHandler(tmp_db_path)
        handler._aggregate = False
        handler.accounts = None

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
            handler._handle_batch_delete()

        mock_delete_bg.assert_called_once_with(tmp_db_path, handler.mail_config)
        handler._redirect.assert_called_once_with("/board", code=302)
