"""Tests for the LLM-driven draft-reply generation module.

These exercise ``src/robotsix_auto_mail/draft/__init__.py`` with the LLM
provider fully mocked — no network calls.
"""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.db import (
    MailRecord,
    get_record_by_message_id,
    init_db,
    insert_record,
)
from robotsix_auto_mail.draft import (
    DraftGenerationError,
    DraftResult,
    _build_draft_user_message,
    generate_draft_reply,
)


def _patch_llm(
    result_obj: DraftResult,
) -> tuple[mock.MagicMock, mock._patch[mock.MagicMock]]:
    """Patch get_provider to return *result_obj* from the LLM.

    Returns the mock handle (to assert ``close()``) and the patcher.
    """
    mock_run_result = mock.MagicMock()
    mock_run_result.output = result_obj
    mock_handle = mock.MagicMock()
    mock_handle.run_sync.return_value = mock_run_result

    mock_provider = mock.MagicMock()
    mock_provider.build_agent.return_value = mock_handle
    mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

    patcher = mock.patch(
        "robotsix_llmio.core.get_provider",
        return_value=mock_provider,
    )
    return mock_handle, patcher


def _insert_inbox(conn: object, message_id: str, **overrides: str) -> None:
    """Insert an inbox MailRecord with sensible defaults."""
    record = MailRecord(
        message_id=message_id,
        sender=overrides.get("sender", "alice@example.com"),
        subject=overrides.get("subject", "Hello"),
        date="2025-06-01T12:00:00",
        status=overrides.get("status", "to_read"),
        body_plain=overrides.get("body_plain", "Can we meet next week?"),
        notes=overrides.get("notes", ""),
    )
    insert_record(conn, record)  # type: ignore[arg-type]


def test_generate_draft_reply_returns_and_persists() -> None:
    """The mocked draft text is returned and persisted to draft_text."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "mid-1")
        mock_handle, patcher = _patch_llm(
            DraftResult(draft_text="Sure, [your availability]. [Your name]")
        )
        with patcher:
            draft = generate_draft_reply(conn, "mid-1", api_key="sk-test")

        assert draft == "Sure, [your availability]. [Your name]"
        record = get_record_by_message_id(conn, "mid-1")
        assert record is not None
        assert record.draft_text == "Sure, [your availability]. [Your name]"
        mock_handle.close.assert_called_once()
    finally:
        conn.close()


def test_build_draft_user_message_includes_notes() -> None:
    """Non-empty notes are appended under a labelled section."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "mid-notes", notes="decline politely")
        record = get_record_by_message_id(conn, "mid-notes")
        assert record is not None
        message = _build_draft_user_message(record)
        assert "User notes / instructions" in message
        assert "decline politely" in message
    finally:
        conn.close()


def test_build_draft_user_message_omits_empty_notes() -> None:
    """Empty/whitespace notes produce no notes section."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "mid-empty", notes="   ")
        record = get_record_by_message_id(conn, "mid-empty")
        assert record is not None
        message = _build_draft_user_message(record)
        assert "User notes / instructions" not in message
    finally:
        conn.close()


def test_generate_draft_reply_missing_record_raises() -> None:
    """A missing message_id raises DraftGenerationError."""
    conn = init_db(":memory:")
    try:
        with pytest.raises(DraftGenerationError):
            generate_draft_reply(conn, "does-not-exist", api_key="sk-test")
    finally:
        conn.close()
