"""Tests for draft management operations (move-to-draft, save-draft, generate-draft)."""

from __future__ import annotations

import re
from unittest import mock

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import init_db
from tests.server.conftest_helpers import (
    _populate_db,
    _post_form,
    _start_test_server,
    _start_test_server_with_mail_config,
)


def test_move_to_draft_ready(single_db: str) -> None:
    """POST /move with triage_action=DRAFT_READY moves to the DRAFT_READY column."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "draft-move",
                "sender": "x@x.com",
                "subject": "Draft move test",
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
            {"message_id": "draft-move", "triage_action": "DRAFT_READY"},
            path="/move",
        )
        assert status == 302, f"Expected 302, got {status}: {body}"

        # Verify the DRAFT_READY column appears with count=1.
        from urllib.request import urlopen

        resp = urlopen(f"http://127.0.0.1:{port}/board")
        board_html = resp.read().decode("utf-8")
        # The DRAFT_READY column header should be "Draft ready"
        assert "Draft ready" in board_html
        counts = re.findall(
            r'<span class="board-column-count">(\d+)</span>',
            board_html,
        )
        assert "1" in counts, f"Unexpected counts: {counts}"
    finally:
        server.shutdown()


def test_save_draft_moves_to_draft_ready(single_db: str) -> None:
    """POST /save-draft persists draft_text and moves the card to DRAFT_READY."""
    # Populate and pre-move to TO_ANSWER.
    _populate_db(
        single_db,
        [
            {
                "message_id": "save-draft-test",
                "sender": "y@y.com",
                "subject": "Save draft test",
                "date": "2025-02-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    server, port = _start_test_server(single_db)
    try:
        # Move to TO_ANSWER first.
        status, _ = _post_form(
            port,
            {"message_id": "save-draft-test", "triage_action": "TO_ANSWER"},
            path="/move",
        )
        assert status == 302

        # Now save a draft.
        status, body = _post_form(
            port,
            {
                "message_id": "save-draft-test",
                "draft_text": "Hello, this is a draft reply.",
            },
            path="/save-draft",
        )
        assert status == 302, f"Expected 302, got {status}: {body}"

        # Verify via direct DB query.
        conn = init_db(single_db)
        try:
            cur = conn.execute(
                "SELECT draft_text FROM mail_records WHERE message_id = ?",
                ("save-draft-test",),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "Hello, this is a draft reply."

            from robotsix_auto_mail.triage import get_triage_decision

            decision = get_triage_decision(conn, "save-draft-test")
            assert decision is not None
            assert decision.action == "DRAFT_READY"
            assert decision.source == "user"
        finally:
            conn.close()
    finally:
        server.shutdown()


def test_save_draft_missing_message_id_returns_400() -> None:
    """POST /save-draft without message_id returns 400."""
    server, port = _start_test_server(":memory:")
    try:
        status, body = _post_form(
            port,
            {"draft_text": "some text"},
            path="/save-draft",
        )
        assert status == 400
        assert "Missing message_id" in body
    finally:
        server.shutdown()


def test_generate_draft_generates_and_moves_to_draft_ready(single_db: str) -> None:
    """POST /generate-draft stores an LLM draft and moves to DRAFT_READY."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "gen-draft-test",
                "sender": "y@y.com",
                "subject": "Question",
                "date": "2025-02-01T00:00:00",
                "body_plain": "Can we meet?",
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

    mock_run_result = mock.MagicMock()
    mock_run_result.output = mock.MagicMock(draft_text="Yes, [your time].")
    mock_handle = mock.MagicMock()
    mock_handle.run_sync.return_value = mock_run_result
    mock_provider = mock.MagicMock()
    mock_provider.build_agent.return_value = mock_handle
    mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

    with mock.patch(
        "robotsix_llmio.core.factory.get_provider_for_identifier",
        return_value=mock_provider,
    ):
        server, port = _start_test_server_with_mail_config(single_db, mail_config)
        try:
            status, body = _post_form(
                port,
                {"message_id": "gen-draft-test"},
                path="/generate-draft",
            )
            assert status == 302, f"Expected 302, got {status}: {body}"
        finally:
            server.shutdown()

    conn = init_db(single_db)
    try:
        cur = conn.execute(
            "SELECT draft_text FROM mail_records WHERE message_id = ?",
            ("gen-draft-test",),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "Yes, [your time]."

        from robotsix_auto_mail.triage import get_triage_decision

        decision = get_triage_decision(conn, "gen-draft-test")
        assert decision is not None
        assert decision.action == "DRAFT_READY"
    finally:
        conn.close()


def test_generate_draft_missing_message_id_returns_400() -> None:
    """POST /generate-draft without message_id returns 400."""
    server, port = _start_test_server(":memory:")
    try:
        status, body = _post_form(
            port,
            {},
            path="/generate-draft",
        )
        assert status == 400
        assert "Missing message_id" in body
    finally:
        server.shutdown()
