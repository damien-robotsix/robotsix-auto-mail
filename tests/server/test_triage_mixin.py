"""Unit tests for ``_TriageMixin`` methods and ``_rules_path_str``.

Drives the mixin directly against a mock handler *self*, isolating the
logic from the HTTP transport and covering the error branches that
integration tests in ``test_server_triage.py`` miss.
"""

from __future__ import annotations

import json
from unittest import mock

from robotsix_auto_mail.core._constants import _TRIAGE_RUN_STATE_KEY
from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.server._triage_mixin import _rules_path_str, _TriageMixin
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


# ===================================================================
# _rules_path_str
# ===================================================================


class TestRulesPathStr:
    def test_mail_config_none_memory_db_returns_none(self) -> None:
        """When mail_config is None and db_path is :memory:, returns None."""
        result = _rules_path_str(None, ":memory:")
        assert result is None

    def test_mail_config_none_file_db_returns_derived_path(self) -> None:
        """When mail_config is None and db_path is a file path, returns
        ``<db-dir>/triage_rules.md``."""
        result = _rules_path_str(None, "/home/user/mail.db")
        assert result == "/home/user/triage_rules.md"

    def test_mail_config_with_triage_rules_path(self) -> None:
        """When mail_config.triage_rules_path is set, that path wins."""
        cfg = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            triage_rules_path="/custom/rules.md",
        )
        result = _rules_path_str(cfg, "/home/user/mail.db")
        assert result == "/custom/rules.md"

    def test_mail_config_without_triage_rules_path(self) -> None:
        """When mail_config has no triage_rules_path, falls back to db-derived."""
        cfg = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        result = _rules_path_str(cfg, "/home/user/mail.db")
        assert result == "/home/user/triage_rules.md"

    def test_mail_config_explicit_empty_string(self) -> None:
        """When triage_rules_path is explicitly empty, db-derived path wins."""
        cfg = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            triage_rules_path="",
        )
        result = _rules_path_str(cfg, "/data/mail.db")
        assert result == "/data/triage_rules.md"


# ===================================================================
# _handle_run_triage
# ===================================================================


class TestHandleRunTriage:
    def test_launches_background_worker_with_correct_args(self) -> None:
        """Delegates to _launch_background_worker with triage watermark,
        target, and args tuple (db_path, user_email, rules_path)."""
        cfg = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
            password="test",
            triage_rules_path="/rules/test.md",
        )
        handler = _FakeHandler("/data/mail.db", mail_config=cfg)
        handler._handle_run_triage()

        handler._launch_background_worker.assert_called_once_with(
            _TRIAGE_RUN_STATE_KEY,
            _run_triage_background,
            ("/data/mail.db", "user@example.com", "/rules/test.md"),
        )

    def test_launches_with_none_email_when_no_config(self) -> None:
        """When mail_config is None, user_email and rules_path are None."""
        handler = _FakeHandler(":memory:", mail_config=None)
        handler._handle_run_triage()

        handler._launch_background_worker.assert_called_once_with(
            _TRIAGE_RUN_STATE_KEY,
            _run_triage_background,
            (":memory:", None, None),
        )

    def test_launches_with_none_rules_when_memory_db_no_config(self) -> None:
        """When db is :memory: and no config, rules_path resolves to None."""
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
            (":memory:", "u@x.com", None),
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
        _bad_request is called with the error message and the worker is
        not launched."""
        handler = _FakeHandler(tmp_db_path)
        handler._parse_request_body.return_value = {"action": "TO_ARCHIVE"}

        with mock.patch(
            "robotsix_auto_mail.triage.delete_triage_decisions_by_action",
            side_effect=TriageError("no decisions to clear"),
        ):
            handler._handle_force_triage_column()

        handler._bad_request.assert_called_once_with("no decisions to clear")
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

        handler._send_response.assert_called_once()
        call_args = handler._send_response.call_args
        assert call_args[1]["status"] == 503
        assert call_args[1]["content_type"] == "application/json"
        body = json.loads(call_args[0][0])
        assert body == {"error": "database is locked"}
        handler._launch_background_worker.assert_not_called()

    # -- valid action, success path ----------------------------------------

    def test_valid_action_clears_decisions_and_launches(self, tmp_db_path: str) -> None:
        """When the action is valid and deletion succeeds, the worker is
        launched with the correct arguments."""
        cfg = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
            password="test",
            triage_rules_path="/rules/test.md",
        )
        handler = _FakeHandler(tmp_db_path, mail_config=cfg)
        handler._parse_request_body.return_value = {"action": "TO_ARCHIVE"}

        with mock.patch(
            "robotsix_auto_mail.triage.delete_triage_decisions_by_action",
        ) as mock_delete:
            handler._handle_force_triage_column()

        # Verify the delete function was called with a connection and the action.
        mock_delete.assert_called_once()
        # The first positional arg is the sqlite3 connection.
        # The second is the action string.
        assert mock_delete.call_args[0][1] == "TO_ARCHIVE"

        # Verify the worker is launched.
        handler._launch_background_worker.assert_called_once_with(
            _TRIAGE_RUN_STATE_KEY,
            _run_triage_background,
            (tmp_db_path, "user@example.com", "/rules/test.md"),
        )

    def test_valid_action_no_mail_config(self, tmp_db_path: str) -> None:
        """When mail_config is None, username is None; rules_path is derived
        from db_path (since no explicit path is configured)."""
        handler = _FakeHandler(tmp_db_path, mail_config=None)
        handler._parse_request_body.return_value = {"action": "HUMAN_TRIAGE"}

        with mock.patch(
            "robotsix_auto_mail.triage.delete_triage_decisions_by_action",
        ):
            handler._handle_force_triage_column()

        # _rules_path_str resolves from db_path when mail_config is None:
        # <db-dir>/triage_rules.md
        import pathlib

        expected_rules = str(pathlib.Path(tmp_db_path).parent / "triage_rules.md")
        handler._launch_background_worker.assert_called_once_with(
            _TRIAGE_RUN_STATE_KEY,
            _run_triage_background,
            (tmp_db_path, None, expected_rules),
        )

    # -- empty action (still invalid) --------------------------------------

    def test_empty_action_returns_400(self, tmp_db_path: str) -> None:
        """An empty string is not a valid triage action."""
        handler = _FakeHandler(tmp_db_path)
        handler._parse_request_body.return_value = {"action": ""}

        handler._handle_force_triage_column()

        handler._bad_request.assert_called_once()
        handler._launch_background_worker.assert_not_called()
