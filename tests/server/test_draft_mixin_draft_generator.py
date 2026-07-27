"""Unit tests for draft generator functions.

Tests ``generate_draft_reply``, ``_build_draft_user_message``,
``_build_draft_system_prompt``, and ``DraftResult`` directly against
a mock LLM provider.
"""

from __future__ import annotations

from unittest import mock

import pytest

from tests.server._draft_helpers import _insert_inbox, _patch_llm


class TestDraftGenerator:
    """Tests for the draft generator functions."""

    def test_generate_draft_reply_returns_and_persists(self) -> None:
        """The mocked draft text is returned and persisted to draft_text."""
        from robotsix_auto_mail.db import get_record_by_message_id, init_db
        from robotsix_auto_mail.server._draft_generator import (
            DraftResult,
            generate_draft_reply,
        )

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

    def test_build_draft_user_message_includes_notes(self) -> None:
        """Non-empty notes are appended under a labelled section."""
        from robotsix_auto_mail.db import get_record_by_message_id, init_db
        from robotsix_auto_mail.server._draft_generator import (
            _build_draft_user_message,
        )

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

    def test_build_draft_user_message_omits_empty_notes(self) -> None:
        """Empty/whitespace notes produce no notes section."""
        from robotsix_auto_mail.db import get_record_by_message_id, init_db
        from robotsix_auto_mail.server._draft_generator import (
            _build_draft_user_message,
        )

        conn = init_db(":memory:")
        try:
            _insert_inbox(conn, "mid-empty", notes="   ")
            record = get_record_by_message_id(conn, "mid-empty")
            assert record is not None
            message = _build_draft_user_message(record)
            assert "User notes / instructions" not in message
        finally:
            conn.close()

    def test_generate_draft_reply_missing_record_raises(self) -> None:
        """A missing message_id raises DraftGenerationError."""
        from robotsix_auto_mail.db import init_db
        from robotsix_auto_mail.server._draft_generator import (
            DraftGenerationError,
            generate_draft_reply,
        )

        conn = init_db(":memory:")
        try:
            with pytest.raises(DraftGenerationError):
                generate_draft_reply(conn, "does-not-exist", api_key="sk-test")
        finally:
            conn.close()

    def test_build_draft_system_prompt_contains_required_keywords(self) -> None:
        """The system prompt is non-empty and includes key instructions."""
        from robotsix_auto_mail.server._draft_generator import (
            _build_draft_system_prompt,
        )

        prompt = _build_draft_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        # Expected keywords from the prompt rules:
        for keyword in ("LANGUAGE", "draft_text", "placeholder", "professional"):
            assert keyword in prompt, f"Missing keyword in system prompt: {keyword}"

    def test_generate_draft_reply_llm_error_propagates(self) -> None:
        """LLM failure raises DraftGenerationError wrapping the original."""
        from robotsix_auto_mail.db import init_db
        from robotsix_auto_mail.server._draft_generator import (
            DraftGenerationError,
            DraftResult,
            generate_draft_reply,
        )

        conn = init_db(":memory:")
        try:
            _insert_inbox(conn, "mid-err")
            _, patcher = _patch_llm(DraftResult(draft_text="irrelevant"))
            # Make run_agent raise a non-DraftGenerationError exception
            run_agent_patch = mock.patch(
                "robotsix_llmio.core.run_agent",
                side_effect=ValueError("LLM timeout"),
            )
            with patcher, run_agent_patch:
                with pytest.raises(DraftGenerationError, match="LLM timeout"):
                    generate_draft_reply(conn, "mid-err", api_key="sk-test")
        finally:
            conn.close()

    def test_build_draft_user_message_truncates_long_body(self) -> None:
        """A body exceeding _BODY_CHAR_LIMIT is truncated in the user message."""
        from robotsix_auto_mail.db import get_record_by_message_id, init_db
        from robotsix_auto_mail.server._draft_generator import (
            _BODY_CHAR_LIMIT,
            _build_draft_user_message,
        )

        conn = init_db(":memory:")
        try:
            long_body = "x" * (_BODY_CHAR_LIMIT + 500)
            _insert_inbox(conn, "mid-long", body_plain=long_body)
            record = get_record_by_message_id(conn, "mid-long")
            assert record is not None
            message = _build_draft_user_message(record)
            # The truncated body should appear in the message, but the full one
            # should not.
            expected_truncated = long_body[:_BODY_CHAR_LIMIT]
            assert expected_truncated in message
            assert long_body not in message
            # The message must be shorter than the original body + framing
            assert len(message) < len(long_body)
        finally:
            conn.close()

    def test_draft_result_requires_draft_text(self) -> None:
        """DraftResult enforces that draft_text is required."""
        from pydantic import ValidationError

        from robotsix_auto_mail.server._draft_generator import DraftResult

        DraftResult(draft_text="hello")  # valid — should not raise

        with pytest.raises(ValidationError):
            DraftResult()  # type: ignore[call-arg]

        with pytest.raises(ValidationError):
            DraftResult(draft_text=123)  # type: ignore[arg-type]
