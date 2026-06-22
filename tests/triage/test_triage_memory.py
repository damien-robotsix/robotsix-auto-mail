"""Tests for the human-decision memory ledger."""

from __future__ import annotations

import os
import tempfile
from unittest import mock

import pytest

from robotsix_auto_mail.db import (
    MailRecord,
    init_db,
    insert_record,
)
from robotsix_auto_mail.triage import (
    SenderMemory,
    TriageError,
    TriageItem,
    TriageResult,
    _build_memory_guidance,
    _load_memory,
    record_human_decision,
    run_triage_agent,
)


def _patch_llm(
    result_obj: TriageResult,
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
        "robotsix_llmio.core.get_provider_for_identifier",
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
        body_plain=overrides.get("body_plain", "Just checking in!"),
    )
    insert_record(conn, record)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Human-decision memory ledger
# ---------------------------------------------------------------------------


def test_load_memory_empty_when_unset() -> None:
    """An unwritten memory loads as an empty dict."""
    conn = init_db(":memory:")
    try:
        assert _load_memory(conn) == {}
    finally:
        conn.close()


def test_record_human_decision_creates_entry() -> None:
    """A first decision creates a count-1 entry keyed by lowercased sender."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>", sender="Alice@Example.com")
        record_human_decision(conn, "<a@x.com>", "TO_ARCHIVE")
        memory = _load_memory(conn)
        assert "alice@example.com" in memory
        entry = memory["alice@example.com"]
        assert isinstance(entry, SenderMemory)
        assert entry.action == "TO_ARCHIVE"
        assert entry.count == 1
        assert entry.last_action == "TO_ARCHIVE"
        assert entry.updated_at != ""
    finally:
        conn.close()


def test_record_human_decision_increments_and_tracks_latest() -> None:
    """Repeated decisions increment count and reflect the latest action."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>", sender="alice@example.com")
        _insert_inbox(conn, "<b@x.com>", sender="alice@example.com")
        record_human_decision(conn, "<a@x.com>", "TO_ARCHIVE")
        record_human_decision(conn, "<b@x.com>", "TO_DELETE")
        entry = _load_memory(conn)["alice@example.com"]
        assert entry.action == "TO_DELETE"
        assert entry.count == 2
        assert entry.last_action == "TO_ARCHIVE"
    finally:
        conn.close()


def test_record_human_decision_rejects_invalid_action() -> None:
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        with pytest.raises(TriageError):
            record_human_decision(conn, "<a@x.com>", "banana")
    finally:
        conn.close()


def test_record_human_decision_unknown_message_is_noop() -> None:
    """An unknown message_id records nothing."""
    conn = init_db(":memory:")
    try:
        record_human_decision(conn, "<missing@x.com>", "TO_ARCHIVE")
        assert _load_memory(conn) == {}
    finally:
        conn.close()


def test_memory_persists_across_connections() -> None:
    """Memory written on one connection is visible on another."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn1 = init_db(path)
        _insert_inbox(conn1, "<a@x.com>", sender="bob@example.com")
        record_human_decision(conn1, "<a@x.com>", "TO_ANSWER")
        conn1.close()

        conn2 = init_db(path)
        entry = _load_memory(conn2)["bob@example.com"]
        assert entry.action == "TO_ANSWER"
        assert entry.count == 1
        conn2.close()
    finally:
        os.unlink(path)


def test_agent_decisions_do_not_update_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_triage_agent (source='agent') leaves the human memory empty."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        _handle, patcher = _patch_llm(
            TriageResult(items=[TriageItem(index=1, action="TO_ARCHIVE")])
        )
        with patcher:
            run_triage_agent(conn)
        assert _load_memory(conn) == {}
    finally:
        conn.close()


def test_build_memory_guidance_empty() -> None:
    """Guidance is the empty string when the memory is empty."""
    conn = init_db(":memory:")
    try:
        assert _build_memory_guidance(conn) == ""
    finally:
        conn.close()


def test_build_memory_guidance_includes_sender_and_action() -> None:
    """Guidance names the sender and the remembered action."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>", sender="alice@example.com")
        record_human_decision(conn, "<a@x.com>", "TO_ARCHIVE")
        guidance = _build_memory_guidance(conn)
        assert "alice@example.com" in guidance
        assert "TO_ARCHIVE" in guidance
    finally:
        conn.close()


def test_run_triage_agent_prompt_includes_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When memory is non-empty, the LLM prompt carries the guidance."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>", sender="alice@example.com")
        record_human_decision(conn, "<a@x.com>", "TO_ARCHIVE")
        handle, patcher = _patch_llm(
            TriageResult(items=[TriageItem(index=1, action="TO_ARCHIVE")])
        )
        with (
            patcher,
            mock.patch("robotsix_auto_mail.triage.agent.propose_archive_subfolder_llm"),
        ):
            run_triage_agent(conn)
        prompt = handle.run_sync.call_args.args[0]
        assert "alice@example.com" in prompt
        assert "triaged by the user as `TO_ARCHIVE`" in prompt
    finally:
        conn.close()


def test_run_triage_agent_prompt_omits_guidance_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty memory keeps the guidance out of the LLM prompt."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        handle, patcher = _patch_llm(
            TriageResult(items=[TriageItem(index=1, action="TO_ARCHIVE")])
        )
        with patcher:
            run_triage_agent(conn)
        prompt = handle.run_sync.call_args.args[0]
        assert "triaged by the user" not in prompt
    finally:
        conn.close()
