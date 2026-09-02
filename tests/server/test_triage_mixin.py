"""Unit tests for ``_TriageMixin`` methods.

Drives the mixin directly against a mock handler *self*, isolating the
logic from the HTTP transport and covering the error branches that
integration tests in ``test_server_triage.py`` miss.
"""

from __future__ import annotations

from unittest import mock

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.core._constants import _TRIAGE_RUN_STATE_KEY
from robotsix_auto_mail.server._triage_mixin import _TriageMixin
from robotsix_auto_mail.server.adapters import _run_triage_background
from robotsix_auto_mail.triage.persistence import TriageError

# ---------------------------------------------------------------------------
# Fake handler for _TriageMixin
# ---------------------------------------------------------------------------


class _FakeHandler(_TriageMixin):
    """Concrete handler that wires ``BoardHandlerProtocol`` attributes
    to MagicMock defaults so mixin methods can be called directly."""

    def __init__(
        self,
        db_path: str,
        mail_config: MailConfig | None = None,
    ) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self._parse_request_body = mock.MagicMock()
        self._launch_background_worker = mock.MagicMock()
        self._bad_request = mock.MagicMock()
        self._send_response = mock.MagicMock()
        self._serve_json = mock.MagicMock()

    def _problem(
        self,
        status: int,
        kind: str,
        title: str,
        detail: str,
        instance: str | None = None,
    ) -> None:
        """Mirror ``BoardHandler._problem`` so mixin calls hit ``_serve_json``."""
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


# ===================================================================
# _handle_run_triage
# ===================================================================


class TestHandleRunTriage:
    def test_launches_background_worker_with_correct_args(self) -> None:
        """Delegates to _launch_background_worker with triage watermark,
        target, and args tuple (db_path, user_email, guidance)."""
        cfg = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
            password="test",
            triage_guidance="archive newsletters to Newsletters",
        )
        handler = _FakeHandler("/data/mail.db", mail_config=cfg)
        handler._handle_run_triage()

        handler._launch_background_worker.assert_called_once_with(
            _TRIAGE_RUN_STATE_KEY,
            _run_triage_background,
            ("/data/mail.db", "user@example.com", "archive newsletters to Newsletters"),
        )

    def test_launches_with_none_email_when_no_config(self) -> None:
        """When mail_config is None, user_email is None and guidance is empty."""
        handler = _FakeHandler(":memory:", mail_config=None)
        handler._handle_run_triage()

        handler._launch_background_worker.assert_called_once_with(
            _TRIAGE_RUN_STATE_KEY,
            _run_triage_background,
            (":memory:", None, ""),
        )

    def test_launches_with_empty_guidance_when_no_guidance_set(self) -> None:
        """When triage_guidance is empty (default), guidance is empty string."""
        cfg = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="u@x.com",
            password="test",
        )
        handler = _FakeHandler(":memory:", mail_config=cfg)
        handler._handle_run_triage()

        handler._launch_background_worker.assert_called_once_with(
            _TRIAGE_RUN_STATE_KEY,
            _run_triage_background,
            (":memory:", "u@x.com", ""),
        )


# ===================================================================
# _handle_force_triage_column
# ===================================================================


class TestHandleForceTriageColumn:
    # -- invalid action ---------------------------------------------------

    def test_invalid_action_returns_400(self, tmp_db_path: str) -> None:
        """When the action is not in VALID_TRIAGE_ACTIONS, _bad_request is
        called and the worker is not launched."""
        handler = _FakeHandler(tmp_db_path)
        handler._parse_request_body.return_value = {"action": "NOT_A_REAL_ACTION"}

        handler._handle_force_triage_column()

        handler._bad_request.assert_called_once()
        assert "Invalid triage action" in str(handler._bad_request.call_args[0][0])
        handler._launch_background_worker.assert_not_called()

    # -- TriageError from delete_triage_decisions_by_action ----------------

    def test_triage_error_returns_400(self, tmp_db_path: str) -> None:
        """When delete_triage_decisions_by_action raises TriageError,
        _bad_request is called with a generic message and the worker is
        not launched."""
        handler = _FakeHandler(tmp_db_path)
        handler._parse_request_body.return_value = {"action": "TO_ARCHIVE"}

        with mock.patch(
            "robotsix_auto_mail.triage.delete_triage_decisions_by_action",
            side_effect=TriageError("no decisions to clear"),
        ):
            handler._handle_force_triage_column()

        handler._bad_request.assert_called_once_with("Invalid request")
        handler._launch_background_worker.assert_not_called()

    # -- generic Exception from delete_triage_decisions_by_action ----------

    def test_generic_exception_returns_503(self, tmp_db_path: str) -> None:
        """When delete_triage_decisions_by_action raises a generic
        exception, _send_response is called with status 503 and JSON body."""
        handler = _FakeHandler(tmp_db_path)
        handler._parse_request_body.return_value = {"action": "TO_DELETE"}

        with mock.patch(
            "robotsix_auto_mail.triage.delete_triage_decisions_by_action",
            side_effect=RuntimeError("database is locked"),
        ):
            handler._handle_force_triage_column()

        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args
        assert call_args[1]["status"] == 503
        assert call_args[0][0]["type"] == "urn:robotsix:error:triage-failed"
