"""Unit tests for ``_handle_batch_archive`` (full-column archive with
background worker)."""

from __future__ import annotations

from unittest import mock

from robotsix_auto_mail.config import DEFAULT_ARCHIVE_ROOT
from robotsix_auto_mail.core._constants import _BATCH_OP_STATE_KEY
from robotsix_auto_mail.db import init_db, set_watermark
from tests.server._test_helpers import _BatchFakeHandler, _SyncThread


class TestHandleBatchArchive:
    """Unit tests for ``_handle_batch_archive``."""

    def test_launches_background_worker_with_correct_args(
        self, tmp_db_path: str
    ) -> None:
        """``_handle_batch_archive`` calls ``_launch_background_worker`` with
        the archive-specific arguments and the configured archive root."""
        handler = _BatchFakeHandler(tmp_db_path)

        with (
            mock.patch.object(handler, "_launch_background_worker") as mock_launch,
            mock.patch(
                "robotsix_auto_mail.server._batch_mixin._run_batch_archive_background"
            ) as mock_archive_bg,
            mock.patch(
                "robotsix_auto_mail.server._batch_mixin._collect_records_for_action"
            ) as mock_collect,
            mock.patch(
                "robotsix_auto_mail.server._batch_mixin._batch_op_running"
            ) as mock_running_check,
        ):
            handler._handle_batch_archive()

            mock_launch.assert_called_once()
            call_args, call_kwargs = mock_launch.call_args
            assert call_args[0] == _BATCH_OP_STATE_KEY
            assert call_args[1] is mock_archive_bg
            assert call_kwargs["running_check"] is mock_running_check

            # The args tuple should be (db_path, mail_config, archive_root, None)
            args_tuple = call_args[2]
            assert args_tuple[0] == tmp_db_path
            assert args_tuple[1] is handler.mail_config
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
        handler = _BatchFakeHandler(tmp_db_path)
        handler.mail_config = None

        with (
            mock.patch.object(handler, "_launch_background_worker") as mock_launch,
            mock.patch(
                "robotsix_auto_mail.server._batch_mixin._run_batch_archive_background"
            ),
        ):
            handler._handle_batch_archive()

        args_tuple = mock_launch.call_args[0][2]
        # Third positional arg is archive_root — should be DEFAULT_ARCHIVE_ROOT
        assert args_tuple[2] == DEFAULT_ARCHIVE_ROOT

    def test_with_subfolder_passes_subfolder_in_args(self, tmp_db_path: str) -> None:
        """When called with a *subfolder*, the fourth element of the args
        tuple carries that subfolder value."""
        handler = _BatchFakeHandler(tmp_db_path)

        with (
            mock.patch.object(handler, "_launch_background_worker") as mock_launch,
            mock.patch(
                "robotsix_auto_mail.server._batch_mixin._run_batch_archive_background"
            ),
        ):
            handler._handle_batch_archive(subfolder="Work/Projects")

        args_tuple = mock_launch.call_args[0][2]
        assert args_tuple[3] == "Work/Projects"

    def test_archive_column_guard_prevents_spawn_when_running(
        self, tmp_db_path: str
    ) -> None:
        """When the ``batch_op:state`` watermark is already "running",
        ``_handle_batch_archive`` does not spawn the archive worker."""
        conn = init_db(tmp_db_path, skip_migrations=True)
        set_watermark(conn, _BATCH_OP_STATE_KEY, "running")
        conn.close()

        handler = _BatchFakeHandler(tmp_db_path)
        mock_archive_bg = mock.MagicMock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._batch_mixin._run_batch_archive_background",
                mock_archive_bg,
            ),
            mock.patch(
                "robotsix_auto_mail.server._action_mixin.threading.Thread",
                _SyncThread,
            ),
        ):
            handler._handle_batch_archive()

        mock_archive_bg.assert_not_called()
        handler._redirect.assert_called_once_with("/board", code=302)

    def test_archive_precheck_empty_column_skips_worker(self, tmp_db_path: str) -> None:
        """When no records have a ``TO_ARCHIVE`` triage decision the
        precheck returns False and no background worker is spawned."""
        handler = _BatchFakeHandler(tmp_db_path)
        mock_archive_bg = mock.MagicMock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._batch_mixin._run_batch_archive_background",
                mock_archive_bg,
            ),
            mock.patch(
                "robotsix_auto_mail.server._action_mixin.threading.Thread",
                _SyncThread,
            ),
        ):
            handler._handle_batch_archive()

        mock_archive_bg.assert_not_called()
        handler._redirect.assert_called_once_with("/board", code=302)
