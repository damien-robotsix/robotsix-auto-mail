"""Unit tests for ``_handle_move``.

Covers triage-action validation, persistence, and TO_ARCHIVE
LLM proposal paths.
"""

from __future__ import annotations

import sqlite3
from unittest import mock

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import init_db
from tests.server._test_helpers import _FakeHandler
from tests.server.conftest_helpers import _populate_db


class TestHandleMove:
    def test_missing_triage_action_returns_400(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "mov-me",
                    "sender": "x@x.com",
                    "subject": "Move",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 50
        handler.rfile.read.return_value = (
            b"message_id=mov-me&triage_action=&redirect_to=/board"
        )

        handler._handle_move()
        handler._bad_request.assert_called_once()
        assert "Missing triage_action" in str(handler._bad_request.call_args[0][0])

    def test_invalid_triage_action_returns_400(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "mov-me",
                    "sender": "x@x.com",
                    "subject": "Move",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 60
        handler.rfile.read.return_value = (
            b"message_id=mov-me&triage_action=NOT_AN_ACTION&redirect_to=/board"
        )

        handler._handle_move()
        handler._bad_request.assert_called_once()
        assert "Invalid triage action" in str(handler._bad_request.call_args[0][0])

    def test_valid_move_persists_decision(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "valid-move",
                    "sender": "x@x.com",
                    "subject": "Test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 80
        handler.rfile.read.return_value = (
            b"message_id=valid-move&triage_action=TO_DELETE&redirect_to=/board"
        )

        handler._handle_move()

        # Verify DB state.
        conn = init_db(single_db)
        try:
            from robotsix_auto_mail.triage import get_triage_decision

            decision = get_triage_decision(conn, "valid-move")
            assert decision is not None
            assert decision.action == "TO_DELETE"
            assert decision.source == "user"
        finally:
            conn.close()

    def test_move_integrity_error_returns_400(self, single_db: str) -> None:
        """When ``set_triage_decision`` raises ``IntegrityError``,
        ``_bad_request`` is called and the redirect is skipped."""
        _populate_db(
            single_db,
            [
                {
                    "message_id": "integ2",
                    "sender": "x@x.com",
                    "subject": "Test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 70
        handler.rfile.read.return_value = (
            b"message_id=integ2&triage_action=TO_DELETE&redirect_to=/board"
        )

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.set_triage_decision",
            side_effect=sqlite3.IntegrityError("CHECK constraint failed"),
        ):
            handler._handle_move()

        handler._bad_request.assert_called_once()
        assert "Could not move" in str(handler._bad_request.call_args[0][0])

    # -- TO_ARCHIVE --------------------------------------------------------

    def test_to_archive_invokes_llm_proposal(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-llm",
                    "sender": "x@x.com",
                    "subject": "Archive me",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            llm_api_key="sk-test",
            llm_provider_model="openrouter-deepseek",
        )
        handler = _FakeHandler(single_db, mail_config=mail_config)
        handler.headers.get.return_value = 90
        handler.rfile.read.return_value = (
            b"message_id=arch-llm&triage_action=TO_ARCHIVE&redirect_to=/board"
        )

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.propose_archive_subfolder_llm"
        ) as mock_propose:
            handler._handle_move()

        mock_propose.assert_called_once()
        # Verify the call arguments.
        call_args = mock_propose.call_args
        assert call_args[0][1].message_id == "arch-llm"  # record (pos 1)
        assert call_args[0][2] == "sk-test"  # api_key (pos 2)
        assert call_args[1] == {
            "provider_model": "openrouter-deepseek",
            "rules": "",
        }  # provider_model + rules kwargs

    def test_to_archive_llm_exception_is_swallowed(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-llm-err",
                    "sender": "x@x.com",
                    "subject": "Archive LLM err",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            llm_api_key="sk-test",
        )
        handler = _FakeHandler(single_db, mail_config=mail_config)
        handler.headers.get.return_value = 100
        handler.rfile.read.return_value = (
            b"message_id=arch-llm-err&triage_action=TO_ARCHIVE&redirect_to=/board"
        )

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.propose_archive_subfolder_llm",
            side_effect=RuntimeError("LLM timeout"),
        ):
            # Should not raise — the exception is swallowed.
            handler._handle_move()

        # The move still succeeds (redirect happens).
        handler._redirect.assert_called()

    def test_to_archive_no_mail_config_skips_llm(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-no-cfg",
                    "sender": "x@x.com",
                    "subject": "No config",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db, mail_config=None)
        handler.headers.get.return_value = 80
        handler.rfile.read.return_value = (
            b"message_id=arch-no-cfg&triage_action=TO_ARCHIVE&redirect_to=/board"
        )

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.propose_archive_subfolder_llm"
        ) as mock_propose:
            handler._handle_move()

        mock_propose.assert_not_called()
