"""Unit tests for the config-sync and archive-proposal handlers in ``_ConfigMixin``.

These exercise the control-flow and error-handling logic directly
without going through the HTTP server, catching regressions in 503
mapping, path validation, and user-action recording that integration
tests only cover indirectly.
"""

from __future__ import annotations

import builtins
from typing import Any
from unittest import mock

from robotsix_auto_mail.server._config_mixin import _ConfigMixin

# ---------------------------------------------------------------------------
# Fake handler for direct mixin testing
# ---------------------------------------------------------------------------


class _FakeConfigHandler(_ConfigMixin):
    """Concrete handler wiring protocol stubs for direct mixin testing."""

    def __init__(self, db_path: str = ":memory:", mail_config: Any = None) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self._serve_json = mock.MagicMock()
        self._bad_request = mock.MagicMock()
        self._handle_post_action = mock.MagicMock()


# ---------------------------------------------------------------------------
# _handle_config_sync
# ---------------------------------------------------------------------------


class TestHandleConfigSync:
    """Unit tests for ``_ConfigMixin._handle_config_sync``."""

    def test_import_error_returns_503(self):
        """When config_sync_agent cannot be imported, return 503 with a JSON error."""
        handler = _FakeConfigHandler()
        _real_import = builtins.__import__

        def _block_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "robotsix_auto_mail.config.config_sync_agent":
                raise ImportError(f"No module named '{name}'")
            return _real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=_block_import):
            handler._handle_config_sync()

        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args
        payload = call_args[0][0]
        assert isinstance(payload, dict)
        assert "error" in payload
        assert "not installed" in payload["error"]
        assert call_args[1]["status"] == 503

    def test_config_sync_error_returns_503(self):
        """ConfigSyncError → 503 with the exception message as JSON."""
        from robotsix_auto_mail.config.config_sync_agent import (
            ConfigSyncError,
        )

        handler = _FakeConfigHandler()

        with mock.patch(
            "robotsix_auto_mail.server._constants._with_db"
        ) as mock_with_db:
            mock_conn = mock.MagicMock()
            mock_with_db.return_value.__enter__.return_value = mock_conn

            with mock.patch(
                "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
                side_effect=ConfigSyncError("drift detected"),
            ):
                handler._handle_config_sync()

        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args
        assert call_args[0][0] == {"error": "drift detected"}
        assert call_args[1]["status"] == 503

    def test_generic_exception_returns_503(self):
        """Any Exception → 503 with the exception message as JSON."""
        handler = _FakeConfigHandler()

        with mock.patch(
            "robotsix_auto_mail.server._constants._with_db"
        ) as mock_with_db:
            mock_conn = mock.MagicMock()
            mock_with_db.return_value.__enter__.return_value = mock_conn

            with mock.patch(
                "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
                side_effect=RuntimeError("something broke"),
            ):
                handler._handle_config_sync()

        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args
        assert call_args[0][0] == {"error": "something broke"}
        assert call_args[1]["status"] == 503

    def test_success_returns_200_with_model_dump(self):
        """Success → 200 with the ConfigSyncResult serialized as JSON."""
        from robotsix_auto_mail.config.config_sync_agent import (
            ConfigSyncResult,
        )

        handler = _FakeConfigHandler()
        result = ConfigSyncResult(proposals=[])

        with mock.patch(
            "robotsix_auto_mail.server._constants._with_db"
        ) as mock_with_db:
            mock_conn = mock.MagicMock()
            mock_with_db.return_value.__enter__.return_value = mock_conn

            with mock.patch(
                "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
                return_value=result,
            ):
                handler._handle_config_sync()

        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args
        assert call_args[0][0] == result.model_dump()
        assert call_args[1]["status"] == 200

    def test_passes_db_path_and_skip_migrations_false(self):
        """The handler opens the DB with skip_migrations=False."""
        from robotsix_auto_mail.config.config_sync_agent import (
            ConfigSyncResult,
        )

        handler = _FakeConfigHandler(db_path="/var/lib/mail/test.db")

        with mock.patch(
            "robotsix_auto_mail.server._constants._with_db"
        ) as mock_with_db:
            mock_conn = mock.MagicMock()
            mock_with_db.return_value.__enter__.return_value = mock_conn

            with mock.patch(
                "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
                return_value=ConfigSyncResult(proposals=[]),
            ):
                handler._handle_config_sync()

        mock_with_db.assert_called_once_with(
            "/var/lib/mail/test.db",
            skip_migrations=False,
        )


# ---------------------------------------------------------------------------
# _handle_archive_proposal
# ---------------------------------------------------------------------------


class TestHandleArchiveProposal:
    """Unit tests for ``_ConfigMixin._handle_archive_proposal``."""

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _capture_action(handler: _FakeConfigHandler) -> Any:
        """Call _handle_archive_proposal and return the captured action."""
        handler._handle_archive_proposal()
        handler._handle_post_action.assert_called_once()
        call_kwargs = handler._handle_post_action.call_args.kwargs
        return call_kwargs["action"]

    # -- field routing -----------------------------------------------------

    def test_dispatches_correct_fields_to_handle_post_action(self):
        """The method routes message_id, subfolder, and redirect_to."""
        handler = _FakeConfigHandler()

        handler._handle_archive_proposal()

        handler._handle_post_action.assert_called_once()
        args = handler._handle_post_action.call_args[0]
        assert args == ("message_id", "subfolder", "redirect_to")

    # -- subfolder validation ----------------------------------------------

    def test_absolute_path_subfolder_is_rejected(self):
        """A subfolder starting with '/' triggers _bad_request + False."""
        handler = _FakeConfigHandler()
        action = self._capture_action(handler)

        result = action(mock.MagicMock(), mock.MagicMock(), "/board", "/etc/passwd")

        assert result is False
        handler._bad_request.assert_called_once_with(
            "Subfolder must not be an absolute path"
        )

    def test_dot_dot_segment_is_rejected(self):
        """A subfolder containing '..' triggers _bad_request + False."""
        handler = _FakeConfigHandler()
        action = self._capture_action(handler)

        result = action(mock.MagicMock(), mock.MagicMock(), "/board", "INBOX/../etc")

        assert result is False
        handler._bad_request.assert_called_once_with(
            "Subfolder must not contain '..' segments"
        )

    def test_over_256_char_subfolder_is_rejected(self):
        """A subfolder exceeding 256 chars triggers _bad_request + False."""
        handler = _FakeConfigHandler()
        action = self._capture_action(handler)

        result = action(mock.MagicMock(), mock.MagicMock(), "/board", "x" * 257)

        assert result is False
        handler._bad_request.assert_called_once_with(
            "Subfolder exceeds maximum length of 256 characters"
        )

    def test_exactly_256_char_subfolder_is_accepted(self):
        """A subfolder of exactly 256 chars passes validation."""
        handler = _FakeConfigHandler()
        action = self._capture_action(handler)

        conn = mock.MagicMock()
        record = mock.MagicMock()
        record.message_id = "msg-1"

        with mock.patch(
            "robotsix_auto_mail.server._config_mixin.set_archive_subfolder_override"
        ) as mock_set:
            result = action(conn, record, "/board", "x" * 256)

        assert result is True
        mock_set.assert_called_once_with(conn, "msg-1", "x" * 256)

    # -- empty / valid subfolder happy paths --------------------------------

    def test_empty_subfolder_calls_set_override(self):
        """An empty subfolder still calls set_archive_subfolder_override."""
        handler = _FakeConfigHandler(mail_config=mock.MagicMock())
        action = self._capture_action(handler)

        conn = mock.MagicMock()
        record = mock.MagicMock()
        record.message_id = "msg-1"

        with mock.patch(
            "robotsix_auto_mail.server._config_mixin.set_archive_subfolder_override"
        ) as mock_set:
            result = action(conn, record, "/board", "")

        assert result is True
        mock_set.assert_called_once_with(conn, "msg-1", "")

    def test_valid_subfolder_calls_set_override(self):
        """A valid subfolder triggers set_archive_subfolder_override."""
        handler = _FakeConfigHandler(mail_config=mock.MagicMock())
        action = self._capture_action(handler)

        conn = mock.MagicMock()
        record = mock.MagicMock()
        record.message_id = "msg-1"

        with mock.patch(
            "robotsix_auto_mail.server._config_mixin.set_archive_subfolder_override"
        ) as mock_set:
            result = action(conn, record, "/board", "Receipts")

        assert result is True
        mock_set.assert_called_once_with(conn, "msg-1", "Receipts")

    # -- mail_config is None ------------------------------------------------

    def test_null_mail_config_still_calls_set_override(self):
        """When mail_config is None, set_archive_subfolder_override is still called."""
        handler = _FakeConfigHandler(mail_config=None)
        action = self._capture_action(handler)

        conn = mock.MagicMock()
        record = mock.MagicMock()
        record.message_id = "msg-1"

        with mock.patch(
            "robotsix_auto_mail.server._config_mixin.set_archive_subfolder_override"
        ) as mock_set:
            result = action(conn, record, "/board", "Receipts")

        assert result is True
        mock_set.assert_called_once_with(conn, "msg-1", "Receipts")
