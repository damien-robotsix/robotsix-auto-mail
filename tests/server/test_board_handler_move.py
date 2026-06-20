"""Tests for the board handler (HTTP request routing and board rendering)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from unittest import mock
from urllib.request import urlopen

if TYPE_CHECKING:
    pass

from tests.server.conftest import (
    _populate_db,
    _post_form,
    _start_test_server,
    _start_test_server_with_mail_config,
)

from robotsix_auto_mail.config import MailConfig

# ---------------------------------------------------------------------------
# POST /move tests
# ---------------------------------------------------------------------------


def test_move_success_redirects_302(single_db: str) -> None:
    _populate_db(
        single_db,
        [
            {
                "message_id": "move-me",
                "sender": "x@x.com",
                "subject": "Move test",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        status, body = _post_form(
            port,
            {"message_id": "move-me", "triage_action": "TO_ARCHIVE"},
        )
        assert status == 302, f"Expected 302, got {status}: {body}"

        # Verify the card actually moved by checking /board.
        resp = urlopen(f"http://127.0.0.1:{port}/board")
        board_html = resp.read().decode("utf-8")
        # Should be in To archive column — the only non-empty one.
        counts = re.findall(
            r'<span class="board-column-count">(\d+)</span>',
            board_html,
        )
        assert counts == ["1"], f"Unexpected counts: {counts}"
    finally:
        server.shutdown()


def test_move_to_triaging(single_db: str) -> None:
    _populate_db(
        single_db,
        [
            {
                "message_id": "m-triaging",
                "sender": "t@t.com",
                "subject": "Triaging",
                "date": "2025-02-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        status, _ = _post_form(
            port, {"message_id": "m-triaging", "triage_action": "TO_ANSWER"}
        )
        assert status == 302

        resp = urlopen(f"http://127.0.0.1:{port}/board")
        body = resp.read().decode("utf-8")
        counts = re.findall(r'<span class="board-column-count">(\d+)</span>', body)
        assert counts == ["1"]
    finally:
        server.shutdown()


def test_move_to_archive(single_db: str) -> None:
    _populate_db(
        single_db,
        [
            {
                "message_id": "m-archive",
                "sender": "a@a.com",
                "subject": "Archive",
                "date": "2025-03-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        status, _ = _post_form(
            port, {"message_id": "m-archive", "triage_action": "TO_ARCHIVE"}
        )
        assert status == 302

        resp = urlopen(f"http://127.0.0.1:{port}/board")
        body = resp.read().decode("utf-8")
        counts = re.findall(r'<span class="board-column-count">(\d+)</span>', body)
        assert counts == ["1"]
    finally:
        server.shutdown()


def test_move_invalid_status_returns_400(single_db: str) -> None:
    _populate_db(
        single_db,
        [
            {
                "message_id": "bad-status",
                "sender": "x@x.com",
                "subject": "Bad",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        status, body = _post_form(
            port, {"message_id": "bad-status", "triage_action": "bogus"}
        )
        assert status == 400
        assert "Invalid triage action: 'bogus'" in body
    finally:
        server.shutdown()


def test_move_missing_message_id_returns_400() -> None:
    server, port = _start_test_server(":memory:")
    try:
        status, body = _post_form(port, {"triage_action": "TO_ARCHIVE"})
        assert status == 400
        assert "Missing message_id" in body
    finally:
        server.shutdown()


def test_move_missing_status_returns_400(single_db: str) -> None:
    _populate_db(
        single_db,
        [
            {
                "message_id": "missing-status",
                "sender": "x@x.com",
                "subject": "Test",
                "date": "2025-06-01T12:00:00",
                "body_plain": "Hello",
                "status": "to_read",
            },
        ],
    )
    server, port = _start_test_server(single_db)
    try:
        status, body = _post_form(port, {"message_id": "missing-status"})
        assert status == 400
        assert "Missing triage_action" in body
    finally:
        server.shutdown()


def test_move_empty_message_id_returns_400() -> None:
    server, port = _start_test_server(":memory:")
    try:
        status, body = _post_form(
            port,
            {"message_id": "  ", "triage_action": "TO_ARCHIVE"},
        )
        assert status == 400
        assert "Missing message_id" in body
    finally:
        server.shutdown()


def test_move_unknown_message_id_returns_404() -> None:
    server, port = _start_test_server(":memory:")
    try:
        status, body = _post_form(
            port, {"message_id": "does-not-exist", "triage_action": "TO_ARCHIVE"}
        )
        assert status == 404
        assert body == "Not found"
    finally:
        server.shutdown()


def test_move_to_archive_triggers_llm(single_db: str) -> None:
    """Moving to TO_ARCHIVE triggers the LLM provider."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "llm-trigger",
                "sender": "dev@python.org",
                "subject": "PEP discussion",
                "date": "2025-06-01T12:00:00",
                "body_plain": "Let's talk about the new PEP.",
                "status": "to_read",
            },
        ],
    )

    mail_config = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user",
        password="pass",
        llm_api_key="sk-test",
    )

    with mock.patch("robotsix_llmio.core.get_provider") as mock_provider_cls:
        server, port = _start_test_server_with_mail_config(single_db, mail_config)
        try:
            status, body = _post_form(
                port,
                {"message_id": "llm-trigger", "triage_action": "TO_ARCHIVE"},
            )
            assert status == 302, f"Expected 302, got {status}: {body}"
            # LLM provider should have been instantiated
            mock_provider_cls.assert_called_once()
        finally:
            server.shutdown()


def test_move_to_archive_llm_failure_still_redirects(single_db: str) -> None:
    """LLM call fails → POST still returns 302."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "llm-fail",
                "sender": "x@x.com",
                "subject": "Test",
                "date": "2025-06-01T12:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    mail_config = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user",
        password="pass",
        llm_api_key="sk-test",
    )

    mock_provider = mock.MagicMock()
    mock_provider.build_agent.side_effect = RuntimeError("LLM crashed")

    with mock.patch(
        "robotsix_llmio.core.get_provider",
        return_value=mock_provider,
    ):
        server, port = _start_test_server_with_mail_config(single_db, mail_config)
        try:
            status, body = _post_form(
                port,
                {"message_id": "llm-fail", "triage_action": "TO_ARCHIVE"},
            )
            assert status == 302, f"Expected 302, got {status}: {body}"

            # Should be in To archive column — the only non-empty one.
            resp = urlopen(f"http://127.0.0.1:{port}/board")
            board_html = resp.read().decode("utf-8")
            counts = re.findall(
                r'<span class="board-column-count">(\d+)</span>',
                board_html,
            )
            assert counts == ["1"], f"Unexpected counts: {counts}"
        finally:
            server.shutdown()


def test_move_to_other_column_skips_llm(single_db: str) -> None:
    """Moving to TO_ANSWER does NOT trigger the LLM."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "skip-llm",
                "sender": "x@x.com",
                "subject": "Question",
                "date": "2025-06-01T12:00:00",
                "body_plain": "Can you help?",
                "status": "to_read",
            },
        ],
    )

    mail_config = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user",
        password="pass",
        llm_api_key="sk-test",
    )

    with mock.patch("robotsix_llmio.core.get_provider") as mock_provider_cls:
        server, port = _start_test_server_with_mail_config(single_db, mail_config)
        try:
            status, body = _post_form(
                port,
                {"message_id": "skip-llm", "triage_action": "TO_ANSWER"},
            )
            assert status == 302, f"Expected 302, got {status}: {body}"
            # LLM provider should NOT have been instantiated
            mock_provider_cls.assert_not_called()
        finally:
            server.shutdown()
