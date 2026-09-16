"""Unit tests for the config-sync and archive-proposal handlers in ``ConfigService``.

These exercise the control-flow and error-handling logic directly
without going through the HTTP server, catching regressions in 503
mapping, path validation, and user-action recording that integration
tests only cover indirectly.
"""

from __future__ import annotations

import builtins
from typing import Any
from unittest import mock

from robotsix_auto_mail.server._config_service import ConfigService

# ---------------------------------------------------------------------------
# Stub context for direct service testing
# ---------------------------------------------------------------------------


class _StubContext:
    """Stub request context wiring the attributes the service reads."""

    def __init__(self, db_path: str = ":memory:", mail_config: Any = None) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self._serve_json = mock.MagicMock()
        self._bad_request = mock.MagicMock()

    def _problem(
        self,
        status: int,
        kind: str,
        title: str,
        detail: str,
        instance: str | None = None,
    ) -> None:
        """Mirror ``BoardHandler._problem`` so service calls hit ``_serve_json``."""
        self._serve_json(
            {
                "type": f"urn:robotsix:error:{kind}",
                "title": title,
                "detail": detail,
                "instance": instance
                if instance is not None
                else getattr(self, "path", "/"),
            },
            status=status,
        )


def _make_service() -> ConfigService:
    return ConfigService(db_path=":memory:")


# ---------------------------------------------------------------------------
# handle_config_sync
# ---------------------------------------------------------------------------


class TestHandleConfigSync:
    """Unit tests for ``ConfigService.handle_config_sync``."""

    def test_import_error_returns_503(self):
        """When config_sync_agent cannot be imported, return 503 with a JSON error."""
        ctx = _StubContext()
        _real_import = builtins.__import__

        def _block_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "robotsix_auto_mail.config.config_sync_agent":
                raise ImportError(f"No module named '{name}'")
            return _real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=_block_import):
            _make_service().handle_config_sync(ctx)

        ctx._serve_json.assert_called_once()
        call_args = ctx._serve_json.call_args
        payload = call_args[0][0]
        assert isinstance(payload, dict)
        assert payload["type"] == "urn:robotsix:error:config-sync-unavailable"
        assert "not installed" in payload["detail"]
        assert call_args[1]["status"] == 503

    def test_config_sync_error_returns_503(self):
        """ConfigSyncError → 503 with the exception message as JSON."""
        from robotsix_auto_mail.config.config_sync_agent import (
            ConfigSyncError,
        )

        ctx = _StubContext()

        with mock.patch(
            "robotsix_auto_mail.server._constants._with_db"
        ) as mock_with_db:
            mock_conn = mock.MagicMock()
            mock_with_db.return_value.__enter__.return_value = mock_conn

            with mock.patch(
                "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
                side_effect=ConfigSyncError("drift detected"),
            ):
                _make_service().handle_config_sync(ctx)

        ctx._serve_json.assert_called_once()
        call_args = ctx._serve_json.call_args
        payload = call_args[0][0]
        assert payload["type"] == "urn:robotsix:error:config-sync-failed"
        assert payload["detail"] == "drift detected"
        assert call_args[1]["status"] == 503

    def test_generic_exception_returns_503(self):
        """Any Exception → 503 with the exception message as JSON."""
        ctx = _StubContext()

        with mock.patch(
            "robotsix_auto_mail.server._constants._with_db"
        ) as mock_with_db:
            mock_conn = mock.MagicMock()
            mock_with_db.return_value.__enter__.return_value = mock_conn

            with mock.patch(
                "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
                side_effect=RuntimeError("something broke"),
            ):
                _make_service().handle_config_sync(ctx)

        ctx._serve_json.assert_called_once()
        call_args = ctx._serve_json.call_args
        payload = call_args[0][0]
        assert payload["type"] == "urn:robotsix:error:config-sync-failed"
        assert payload["detail"] == "something broke"
        assert call_args[1]["status"] == 503

    def test_success_returns_200_with_model_dump(self):
        """Success → 200 with the ConfigSyncResult serialized as JSON."""
        from robotsix_auto_mail.config.config_sync_agent import (
            ConfigSyncResult,
        )

        ctx = _StubContext()
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
                _make_service().handle_config_sync(ctx)

        ctx._serve_json.assert_called_once()
        call_args = ctx._serve_json.call_args
        assert call_args[0][0] == result.model_dump()
        assert call_args[1]["status"] == 200

    def test_passes_db_path_and_skip_migrations_false(self):
        """The handler opens the DB with skip_migrations=False."""
        from robotsix_auto_mail.config.config_sync_agent import (
            ConfigSyncResult,
        )

        ctx = _StubContext(db_path="/var/lib/mail/test.db")

        with mock.patch(
            "robotsix_auto_mail.server._constants._with_db"
        ) as mock_with_db:
            mock_conn = mock.MagicMock()
            mock_with_db.return_value.__enter__.return_value = mock_conn

            with mock.patch(
                "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
                return_value=ConfigSyncResult(proposals=[]),
            ):
                _make_service().handle_config_sync(ctx)

        mock_with_db.assert_called_once_with(
            "/var/lib/mail/test.db",
            skip_migrations=False,
        )


# ---------------------------------------------------------------------------
# handle_archive_proposal
# ---------------------------------------------------------------------------


class TestHandleArchiveProposal:
    """Unit tests for ``ConfigService.handle_archive_proposal``."""

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _capture_action(ctx: _StubContext) -> Any:
        """Call handle_archive_proposal and return the captured action."""
        with mock.patch(
            "robotsix_auto_mail.server._config_service.handle_post_action"
        ) as mock_hpa:
            _make_service().handle_archive_proposal(ctx)
        mock_hpa.assert_called_once()
        return mock_hpa.call_args.kwargs["action"]

    # -- field routing -----------------------------------------------------

    def test_dispatches_correct_fields_to_handle_post_action(self):
        """The method routes message_id, subfolder, and redirect_to."""
        ctx = _StubContext()

        with mock.patch(
            "robotsix_auto_mail.server._config_service.handle_post_action"
        ) as mock_hpa:
            _make_service().handle_archive_proposal(ctx)

        mock_hpa.assert_called_once()
        args = mock_hpa.call_args[0]
        assert args == (ctx, "message_id", "subfolder", "redirect_to")

    # -- subfolder validation ----------------------------------------------

    def test_absolute_path_subfolder_is_rejected(self):
        """A subfolder starting with '/' triggers _bad_request + False."""
        ctx = _StubContext()
        action = self._capture_action(ctx)

        result = action(mock.MagicMock(), mock.MagicMock(), "/board", "/etc/passwd")

        assert result is False
        ctx._bad_request.assert_called_once_with(
            "Subfolder must not be an absolute path"
        )

    def test_dot_dot_segment_is_rejected(self):
        """A subfolder containing '..' triggers _bad_request + False."""
        ctx = _StubContext()
        action = self._capture_action(ctx)

        result = action(mock.MagicMock(), mock.MagicMock(), "/board", "INBOX/../etc")

        assert result is False
        ctx._bad_request.assert_called_once_with(
            "Subfolder must not contain '..' segments"
        )

    def test_over_256_char_subfolder_is_rejected(self):
        """A subfolder exceeding 256 chars triggers _bad_request + False."""
        ctx = _StubContext()
        action = self._capture_action(ctx)

        result = action(mock.MagicMock(), mock.MagicMock(), "/board", "x" * 257)

        assert result is False
        ctx._bad_request.assert_called_once_with(
            "Subfolder exceeds maximum length of 256 characters"
        )

    def test_exactly_256_char_subfolder_is_accepted(self):
        """A subfolder of exactly 256 chars passes validation."""
        ctx = _StubContext()
        action = self._capture_action(ctx)

        conn = mock.MagicMock()
        record = mock.MagicMock()
        record.message_id = "msg-1"

        with mock.patch(
            "robotsix_auto_mail.server._config_service.set_archive_subfolder_override"
        ) as mock_set:
            result = action(conn, record, "/board", "x" * 256)

        assert result is True
        mock_set.assert_called_once_with(conn, "msg-1", "x" * 256)

    # -- empty / valid subfolder happy paths --------------------------------

    def test_empty_subfolder_calls_set_override(self):
        """An empty subfolder still calls set_archive_subfolder_override."""
        ctx = _StubContext(mail_config=mock.MagicMock())
        action = self._capture_action(ctx)

        conn = mock.MagicMock()
        record = mock.MagicMock()
        record.message_id = "msg-1"

        with mock.patch(
            "robotsix_auto_mail.server._config_service.set_archive_subfolder_override"
        ) as mock_set:
            result = action(conn, record, "/board", "")

        assert result is True
        mock_set.assert_called_once_with(conn, "msg-1", "")

    def test_valid_subfolder_calls_set_override(self):
        """A valid subfolder triggers set_archive_subfolder_override."""
        ctx = _StubContext(mail_config=mock.MagicMock())
        action = self._capture_action(ctx)

        conn = mock.MagicMock()
        record = mock.MagicMock()
        record.message_id = "msg-1"

        with mock.patch(
            "robotsix_auto_mail.server._config_service.set_archive_subfolder_override"
        ) as mock_set:
            result = action(conn, record, "/board", "Receipts")

        assert result is True
        mock_set.assert_called_once_with(conn, "msg-1", "Receipts")

    # -- mail_config is None ------------------------------------------------

    def test_null_mail_config_still_calls_set_override(self):
        """When mail_config is None, set_archive_subfolder_override is still called."""
        ctx = _StubContext(mail_config=None)
        action = self._capture_action(ctx)

        conn = mock.MagicMock()
        record = mock.MagicMock()
        record.message_id = "msg-1"

        with mock.patch(
            "robotsix_auto_mail.server._config_service.set_archive_subfolder_override"
        ) as mock_set:
            result = action(conn, record, "/board", "Receipts")

        assert result is True
        mock_set.assert_called_once_with(conn, "msg-1", "Receipts")
