"""Tests for UnsubscribeDetection model, detection, and check."""

from __future__ import annotations

import json
from unittest import mock

import pydantic
import pytest
from tests.conftest import _make_record

from robotsix_auto_mail.db import (
    get_watermark,
    init_db,
    insert_record,
    set_watermark,
)
from robotsix_auto_mail.triage import (
    _UNSUBSCRIBE_SUGGESTIONS_KEY,
    UnsubscribeDetection,
    _check_unsubscribe_for_to_delete,
    _detect_unsubscribe_for_sender,
    set_triage_decision,
)

# ---------------------------------------------------------------------------
# UnsubscribeDetection model
# ---------------------------------------------------------------------------


def test_unsubscribe_detection_defaults() -> None:
    """method defaults to '', url to '', description to '', confidence to 'medium'."""

    d = UnsubscribeDetection(has_unsubscribe=True)
    assert d.has_unsubscribe is True
    assert d.method == ""
    assert d.url == ""
    assert d.description == ""
    assert d.confidence == "medium"


def test_unsubscribe_detection_requires_has_unsubscribe() -> None:
    """has_unsubscribe is a required field."""

    with pytest.raises(pydantic.ValidationError):
        UnsubscribeDetection()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# _detect_unsubscribe_for_sender — fast path (header present)
# ---------------------------------------------------------------------------


def test_detect_unsubscribe_fast_path_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When unsubscribe_header is non-empty, return detection without LLM call."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")

    records = [
        _make_record(
            message_id="<1@x.com>",
            sender="sender@example.com",
            subject="Newsletter",
            date="2025-06-01T12:00:00",
            body_plain="Hello world",
            unsubscribe_header="<https://example.com/unsub>",
        ),
        _make_record(
            message_id="<2@x.com>",
            sender="sender@example.com",
            subject="Newsletter #2",
            date="2025-06-02T12:00:00",
            body_plain="Hello again",
            unsubscribe_header="<https://example.com/unsub>",
        ),
    ]
    with mock.patch("robotsix_llmio.core.get_provider_for_identifier") as cls:
        result = _detect_unsubscribe_for_sender(
            None,  # conn not used in fast path
            "sender@example.com",
            records,
        )
    assert result is not None
    assert result.has_unsubscribe is True
    assert result.method == "header"
    assert result.url == "https://example.com/unsub"
    assert result.confidence == "high"
    cls.assert_not_called()


def test_detect_unsubscribe_fast_path_mailto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mailto: unsubscribe_header is detected as method='mailto'."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")

    records = [
        _make_record(
            message_id="<1@x.com>",
            sender="sender@example.com",
            subject="Newsletter",
            date="2025-06-01T12:00:00",
            body_plain="Hello",
            unsubscribe_header="<mailto:unsub@example.com>",
        ),
    ]
    with mock.patch("robotsix_llmio.core.get_provider_for_identifier") as cls:
        result = _detect_unsubscribe_for_sender(None, "sender@example.com", records)
    assert result is not None
    assert result.has_unsubscribe is True
    assert result.method == "mailto"
    assert "mailto:" in result.url
    assert result.url == "mailto:unsub@example.com"
    cls.assert_not_called()


# ---------------------------------------------------------------------------
# _detect_unsubscribe_for_sender — LLM path
# ---------------------------------------------------------------------------


def test_detect_unsubscribe_llm_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no header, LLM is called with full body_plain."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")

    records = [
        _make_record(
            message_id="<1@x.com>",
            sender="sender@example.com",
            subject="Newsletter",
            date="2025-06-01T12:00:00",
            body_plain="Click here to unsubscribe: https://example.com/optout",
            unsubscribe_header="",
        ),
    ]

    mock_result_obj = UnsubscribeDetection(
        has_unsubscribe=True,
        method="body_link",
        url="https://example.com/optout",
        description="Unsubscribe link found in body",
        confidence="medium",
    )
    mock_run_result = mock.MagicMock()
    mock_run_result.output = mock_result_obj
    mock_handle = mock.MagicMock()
    mock_handle.run_sync.return_value = mock_run_result
    mock_provider = mock.MagicMock()
    mock_provider.build_agent.return_value = mock_handle
    mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

    with mock.patch(
        "robotsix_llmio.core.get_provider_for_identifier",
        return_value=mock_provider,
    ):
        result = _detect_unsubscribe_for_sender(None, "sender@example.com", records)

    assert result is not None
    assert result.has_unsubscribe is True
    assert result.method == "body_link"
    assert result.url == "https://example.com/optout"

    # Verify the system prompt mentions "unsubscribe".
    build_agent_call = mock_provider.build_agent.call_args
    system_prompt = build_agent_call.kwargs["system_prompt"]
    assert "unsubscribe" in system_prompt.lower()

    # Verify the user message contains the full body_plain.
    user_message = mock_handle.run_sync.call_args.args[0]
    assert "Click here to unsubscribe" in user_message
    assert "sender@example.com" in user_message
    assert "Newsletter" in user_message


def test_detect_unsubscribe_llm_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On LLM failure, _detect_unsubscribe_for_sender returns None gracefully."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")

    records = [
        _make_record(
            message_id="<1@x.com>",
            sender="sender@example.com",
            subject="Newsletter",
            date="2025-06-01T12:00:00",
            body_plain="Hello",
            unsubscribe_header="",
        ),
    ]

    mock_handle = mock.MagicMock()
    mock_handle.run_sync.side_effect = RuntimeError("LLM exploded")
    mock_provider = mock.MagicMock()
    mock_provider.build_agent.return_value = mock_handle
    # Simulate call_with_retry propagating the exception.
    mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

    with mock.patch(
        "robotsix_llmio.core.get_provider_for_identifier",
        return_value=mock_provider,
    ):
        result = _detect_unsubscribe_for_sender(None, "sender@example.com", records)

    assert result is None


# ---------------------------------------------------------------------------
# _check_unsubscribe_for_to_delete
# ---------------------------------------------------------------------------


def test_check_unsubscribe_for_to_delete_populates_watermark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3+ TO_DELETE records from same sender → watermark entry created."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")

    conn = init_db(":memory:")
    try:
        # Insert 3 records from the same sender and mark them TO_DELETE.
        for i in range(3):
            mid = f"<{i}@spam.com>"
            record = _make_record(
                message_id=mid,
                sender="spammer@example.com",
                subject=f"Spam {i}",
                date=f"2025-06-0{i + 1}T12:00:00",
                body_plain="Buy now!",
                unsubscribe_header="<https://unsub.example.com/optout>",
            )
            insert_record(conn, record)
            set_triage_decision(conn, mid, "TO_DELETE", source="agent", reason="spam")

        _check_unsubscribe_for_to_delete(conn)

        raw = get_watermark(conn, _UNSUBSCRIBE_SUGGESTIONS_KEY)
        assert raw is not None
        suggestions = json.loads(raw)
        assert "spammer@example.com" in suggestions
        entry = suggestions["spammer@example.com"]
        assert entry["has_unsubscribe"] is True
        assert entry["method"] == "header"
    finally:
        conn.close()


def test_check_unsubscribe_threshold_not_met(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only 2 TO_DELETE records → no watermark entry created."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")

    conn = init_db(":memory:")
    try:
        for i in range(2):
            mid = f"<{i}@spam.com>"
            record = _make_record(
                message_id=mid,
                sender="spammer@example.com",
                subject=f"Spam {i}",
                date=f"2025-06-0{i + 1}T12:00:00",
                body_plain="Buy now!",
                unsubscribe_header="<https://unsub.example.com/optout>",
            )
            insert_record(conn, record)
            set_triage_decision(conn, mid, "TO_DELETE", source="agent", reason="spam")

        with mock.patch("robotsix_llmio.core.get_provider_for_identifier") as cls:
            _check_unsubscribe_for_to_delete(conn)
        cls.assert_not_called()

        raw = get_watermark(conn, _UNSUBSCRIBE_SUGGESTIONS_KEY)
        assert raw is None
    finally:
        conn.close()


def test_check_unsubscribe_caching_skips_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-populated watermark entry → LLM NOT called again."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")

    conn = init_db(":memory:")
    try:
        # Pre-populate the watermark.
        set_watermark(
            conn,
            _UNSUBSCRIBE_SUGGESTIONS_KEY,
            json.dumps(
                {
                    "spammer@example.com": {
                        "has_unsubscribe": True,
                        "method": "header",
                        "url": "<https://unsub.example.com/optout>",
                        "description": "Already cached",
                        "confidence": "high",
                    }
                }
            ),
        )

        # Insert 3 records and mark TO_DELETE.
        for i in range(3):
            mid = f"<{i}@spam.com>"
            record = _make_record(
                message_id=mid,
                sender="spammer@example.com",
                subject=f"Spam {i}",
                date=f"2025-06-0{i + 1}T12:00:00",
                body_plain="Buy now!",
                unsubscribe_header="<https://unsub.example.com/optout>",
            )
            insert_record(conn, record)
            set_triage_decision(conn, mid, "TO_DELETE", source="agent", reason="spam")

        with mock.patch("robotsix_llmio.core.get_provider_for_identifier") as cls:
            _check_unsubscribe_for_to_delete(conn)
        # LLM provider should NOT be called — caching fast path.
        cls.assert_not_called()
    finally:
        conn.close()


def test_check_unsubscribe_multiple_senders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple senders above threshold each get checked independently."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")

    conn = init_db(":memory:")
    try:
        # Sender A: 3 records with header.
        for i in range(3):
            mid = f"<a{i}@x.com>"
            record = _make_record(
                message_id=mid,
                sender="spammer-a@example.com",
                subject=f"Spam A {i}",
                date=f"2025-06-0{i + 1}T12:00:00",
                body_plain="Buy A!",
                unsubscribe_header="<https://a.example.com/unsub>",
            )
            insert_record(conn, record)
            set_triage_decision(conn, mid, "TO_DELETE", source="agent", reason="spam")

        # Sender B: 3 records with header.
        for i in range(3):
            mid = f"<b{i}@x.com>"
            record = _make_record(
                message_id=mid,
                sender="spammer-b@example.com",
                subject=f"Spam B {i}",
                date=f"2025-06-0{i + 1}T12:00:00",
                body_plain="Buy B!",
                unsubscribe_header="<https://b.example.com/unsub>",
            )
            insert_record(conn, record)
            set_triage_decision(conn, mid, "TO_DELETE", source="agent", reason="spam")

        _check_unsubscribe_for_to_delete(conn)

        raw = get_watermark(conn, _UNSUBSCRIBE_SUGGESTIONS_KEY)
        assert raw is not None
        suggestions = json.loads(raw)
        assert "spammer-a@example.com" in suggestions
        assert "spammer-b@example.com" in suggestions
    finally:
        conn.close()
