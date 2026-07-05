"""Unit tests for ``_BoardActionMixin._launch_background_worker``.

Covers free-watermark spawn + redirect, running-watermark no-spawn,
precheck-false no-spawn, redirect=False suppression, no-target still
acquires watermark, and custom running-check.
"""

from __future__ import annotations

from unittest import mock

from robotsix_auto_mail.db import init_db
from tests.server._test_helpers import _FakeHandler, _SyncThread


class TestLaunchBackgroundWorker:
    def test_free_watermark_spawns_and_redirects(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        target = mock.MagicMock()

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.threading.Thread",
            _SyncThread,
        ):
            result = handler._launch_background_worker(
                "wm:test", target=target, args=(42,)
            )

        assert result is True
        target.assert_called_once_with(42)
        handler._redirect.assert_called_once_with("/board", code=302)

    def test_running_watermark_returns_false_no_spawn(self, tmp_db_path: str) -> None:
        # Seed the watermark as "running".
        conn = init_db(tmp_db_path, skip_migrations=True)
        from robotsix_auto_mail.db import set_watermark

        set_watermark(conn, "wm:locked", "running")
        conn.close()

        handler = _FakeHandler(tmp_db_path)
        target = mock.MagicMock()

        result = handler._launch_background_worker("wm:locked", target=target)
        assert result is False
        target.assert_not_called()
        handler._redirect.assert_called_once_with("/board", code=302)

    def test_precheck_false_returns_false_no_spawn(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        target = mock.MagicMock()
        precheck = mock.MagicMock(return_value=False)

        result = handler._launch_background_worker(
            "wm:precheck", target=target, precheck=precheck
        )
        assert result is False
        target.assert_not_called()
        handler._redirect.assert_called_once_with("/board", code=302)

    def test_redirect_false_does_not_redirect(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        target = mock.MagicMock()

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.threading.Thread",
            _SyncThread,
        ):
            result = handler._launch_background_worker(
                "wm:noredir", target=target, redirect=False
            )

        assert result is True
        target.assert_called_once()
        handler._redirect.assert_not_called()

    def test_no_target_still_acquires_watermark(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)

        result = handler._launch_background_worker(
            "wm:notarget", target=None, redirect=False
        )
        assert result is True
        handler._redirect.assert_not_called()

    def test_custom_running_check(self, tmp_db_path: str) -> None:
        """A custom ``running_check`` that considers any non-None value
        as running prevents spawn."""
        conn = init_db(tmp_db_path, skip_migrations=True)
        from robotsix_auto_mail.db import set_watermark

        set_watermark(conn, "wm:cust", "busy")
        conn.close()

        handler = _FakeHandler(tmp_db_path)
        target = mock.MagicMock()

        def _any_non_none(v: str | None) -> bool:
            return v is not None

        result = handler._launch_background_worker(
            "wm:cust", target=target, running_check=_any_non_none
        )
        assert result is False
        target.assert_not_called()
