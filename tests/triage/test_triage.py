"""Tests for the inbox triage agent and triage-decision persistence.

These exercise ``src/robotsix_auto_mail/triage.py``.
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest import mock

import pydantic
import pytest
from robotsix_llmio.core import Tier
from tests.conftest import _make_record

from robotsix_auto_mail.db import (
    MailRecord,
    get_record_by_message_id,
    get_watermark,
    init_db,
    insert_record,
    list_untriaged_records,
    set_watermark,
    update_sent_reply_text,
)
from robotsix_auto_mail.triage import (
    TRIAGE_ACTION_LABELS,
    TRIAGE_ACTION_ORDER,
    VALID_TRIAGE_ACTIONS,
    ArchiveFolderMemory,
    ArchiveSubfolderProposal,
    RuleLedgerEntry,
    SenderMemory,
    TriageDecision,
    TriageError,
    TriageItem,
    TriageResult,
    TriageRule,
    TriageRuleProposal,
    _build_memory_guidance,
    _build_triage_system_prompt,
    _build_user_message,
    _load_active_rules,
    _load_archive_folder_memory,
    _load_archive_overrides,
    _load_llm_archive_hints,
    _load_memory,
    _load_rule_ledger,
    _rule_fingerprint,
    _save_llm_archive_hints,
    apply_triage_rules,
    delete_triage_decision,
    delete_triage_decisions_by_action,
    get_archive_subfolder,
    get_triage_decision,
    list_rule_proposals,
    list_triage_decisions,
    propose_archive_subfolder,
    propose_archive_subfolder_llm,
    propose_triage_rules,
    record_and_filter_rule_proposals,
    record_archive_folder_choice,
    record_human_decision,
    run_triage_agent,
    set_archive_subfolder_override,
    set_rule_state,
    set_triage_decision,
)


def _patch_llm(
    result_obj: TriageResult,
) -> tuple[mock.MagicMock, mock._patch[mock.MagicMock]]:
    """Patch OpenRouterDeepseekProvider to return *result_obj* from the LLM.

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
        "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider",
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
# Pydantic models
# ---------------------------------------------------------------------------


def test_triage_item_defaults() -> None:
    """action defaults to user_triage, confidence to medium, reason to ''."""
    item = TriageItem(index=1)
    assert item.action == "HUMAN_TRIAGE"
    assert item.confidence == "medium"
    assert item.reason == ""


def test_triage_item_coerces_unknown_action() -> None:
    """An unknown action is coerced to user_triage, not rejected."""
    item = TriageItem(index=1, action="banana")
    assert item.action == "HUMAN_TRIAGE"


def test_triage_item_coerces_inbox_action() -> None:
    """The agent may not assign INBOX; it is coerced to HUMAN_TRIAGE."""
    item = TriageItem(index=1, action="INBOX")
    assert item.action == "HUMAN_TRIAGE"


def test_triage_item_rejects_index_below_one() -> None:
    """index must be >= 1."""
    with pytest.raises(pydantic.ValidationError):
        TriageItem(index=0)


def test_triage_item_rejects_unknown_confidence() -> None:
    """An out-of-set confidence raises a pydantic ValidationError."""
    with pytest.raises(pydantic.ValidationError):
        TriageItem(index=1, confidence="bogus")


def test_triage_result_defaults_empty() -> None:
    """items defaults to an empty list."""
    assert TriageResult().items == []


def test_triage_decision_rejects_invalid_action() -> None:
    with pytest.raises(pydantic.ValidationError):
        TriageDecision(message_id="<a>", action="banana", source="user")


def test_triage_decision_accepts_draft_ready_action() -> None:
    """DRAFT_READY is a valid triage action."""
    decision = TriageDecision(
        message_id="<draft@test.com>", action="DRAFT_READY", source="user"
    )
    assert decision.action == "DRAFT_READY"


def test_triage_decision_rejects_invalid_source() -> None:
    with pytest.raises(pydantic.ValidationError):
        TriageDecision(message_id="<a>", action="TO_ANSWER", source="robot")


def test_triage_error_is_exception() -> None:
    err = TriageError("boom")
    assert isinstance(err, Exception)
    assert str(err) == "boom"


# ---------------------------------------------------------------------------
# set_triage_decision validation
# ---------------------------------------------------------------------------


def test_set_triage_decision_rejects_invalid_action() -> None:
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        with pytest.raises(TriageError):
            set_triage_decision(conn, "<a@x.com>", "banana", source="user")
    finally:
        conn.close()


def test_set_triage_decision_rejects_invalid_source() -> None:
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        with pytest.raises(TriageError):
            set_triage_decision(conn, "<a@x.com>", "TO_ANSWER", source="robot")
    finally:
        conn.close()


def test_set_triage_decision_upserts() -> None:
    """A second call for the same message_id overwrites the first."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        set_triage_decision(conn, "<a@x.com>", "TO_ANSWER", source="agent")
        set_triage_decision(
            conn, "<a@x.com>", "TO_ARCHIVE", source="user", reason="mine"
        )
        decision = get_triage_decision(conn, "<a@x.com>")
        assert decision is not None
        assert decision.action == "TO_ARCHIVE"
        assert decision.source == "user"
        assert decision.reason == "mine"
        # Still exactly one row.
        assert len(list_triage_decisions(conn)) == 1
    finally:
        conn.close()


def test_get_triage_decision_missing_returns_none() -> None:
    conn = init_db(":memory:")
    try:
        assert get_triage_decision(conn, "<nope@x.com>") is None
    finally:
        conn.close()


def test_list_triage_decisions_filters_by_source() -> None:
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        _insert_inbox(conn, "<b@x.com>")
        set_triage_decision(conn, "<a@x.com>", "TO_ANSWER", source="agent")
        set_triage_decision(conn, "<b@x.com>", "TO_ARCHIVE", source="user")
        agent_only = list_triage_decisions(conn, source="agent")
        assert [d.message_id for d in agent_only] == ["<a@x.com>"]
        user_only = list_triage_decisions(conn, source="user")
        assert [d.message_id for d in user_only] == ["<b@x.com>"]
        assert len(list_triage_decisions(conn)) == 2
    finally:
        conn.close()


def test_triage_decision_persists_across_connections() -> None:
    """A decision written on one connection is visible on another."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn1 = init_db(path)
        _insert_inbox(conn1, "<persisted@x.com>")
        set_triage_decision(conn1, "<persisted@x.com>", "TO_ANSWER", source="user")
        conn1.close()

        conn2 = init_db(path)
        decision = get_triage_decision(conn2, "<persisted@x.com>")
        assert decision is not None
        assert decision.action == "TO_ANSWER"
        assert decision.source == "user"
        conn2.close()
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# delete_triage_decisions_by_action
# ---------------------------------------------------------------------------


def test_delete_triage_decisions_by_action_happy_path() -> None:
    """Happy path: deletes all decisions for one action, returns count."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        _insert_inbox(conn, "<b@x.com>")
        _insert_inbox(conn, "<c@x.com>")
        set_triage_decision(conn, "<a@x.com>", "HUMAN_TRIAGE", source="user")
        set_triage_decision(conn, "<b@x.com>", "HUMAN_TRIAGE", source="user")
        set_triage_decision(conn, "<c@x.com>", "TO_ARCHIVE", source="user")

        deleted = delete_triage_decisions_by_action(conn, "HUMAN_TRIAGE")
        assert deleted == 2
        remaining = list_triage_decisions(conn)
        assert len(remaining) == 1
        assert remaining[0].action == "TO_ARCHIVE"
    finally:
        conn.close()


def test_delete_triage_decisions_by_action_rejects_inbox() -> None:
    """action='INBOX' raises TriageError."""
    conn = init_db(":memory:")
    try:
        with pytest.raises(TriageError, match="INBOX"):
            delete_triage_decisions_by_action(conn, "INBOX")
    finally:
        conn.close()


def test_delete_triage_decisions_by_action_invalid_action() -> None:
    """Invalid action raises TriageError."""
    conn = init_db(":memory:")
    try:
        with pytest.raises(TriageError, match="Invalid triage action"):
            delete_triage_decisions_by_action(conn, "BOGUS")
    finally:
        conn.close()


def test_delete_triage_decisions_by_action_no_matching_rows() -> None:
    """Zero matching rows returns 0, no error."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        set_triage_decision(conn, "<a@x.com>", "TO_ARCHIVE", source="user")
        deleted = delete_triage_decisions_by_action(conn, "HUMAN_TRIAGE")
        assert deleted == 0
        assert len(list_triage_decisions(conn)) == 1
    finally:
        conn.close()


def test_delete_triage_decision_requeues_record() -> None:
    """Deleting a record's decision makes it untriaged again."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        set_triage_decision(conn, "<a@x.com>", "TO_ANSWER", source="user")
        assert get_triage_decision(conn, "<a@x.com>") is not None
        assert "<a@x.com>" not in {r.message_id for r in list_untriaged_records(conn)}

        assert delete_triage_decision(conn, "<a@x.com>") is True
        assert get_triage_decision(conn, "<a@x.com>") is None
        assert "<a@x.com>" in {r.message_id for r in list_untriaged_records(conn)}

        # Deleting again is a no-op (no row to remove).
        assert delete_triage_decision(conn, "<a@x.com>") is False
    finally:
        conn.close()


def test_update_sent_reply_text_and_column_default() -> None:
    """sent_reply_text defaults to '' and update_sent_reply_text persists it."""
    conn = init_db(":memory:")
    try:
        # Column present on mail_records.
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(mail_records)").fetchall()
        }
        assert "sent_reply_text" in cols

        _insert_inbox(conn, "<a@x.com>")
        record = get_record_by_message_id(conn, "<a@x.com>")
        assert record is not None
        assert record.sent_reply_text == ""

        assert update_sent_reply_text(conn, "<a@x.com>", "My reply body.") is True
        updated = get_record_by_message_id(conn, "<a@x.com>")
        assert updated is not None
        assert updated.sent_reply_text == "My reply body."

        # No matching row → False.
        assert update_sent_reply_text(conn, "<missing>", "x") is False
    finally:
        conn.close()


def test_answered_record_untriaged_and_marked_in_user_message() -> None:
    """A record with sent_reply_text (and no decision) is untriaged and its
    user-message line carries the answered marker + reply preview."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>", subject="Question")
        update_sent_reply_text(conn, "<a@x.com>", "Thanks, all sorted.")

        untriaged = list_untriaged_records(conn)
        assert "<a@x.com>" in {r.message_id for r in untriaged}

        message = _build_user_message(untriaged)
        assert "ANSWERED — reply sent:" in message
        assert "Thanks, all sorted." in message
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# run_triage_agent
# ---------------------------------------------------------------------------


def test_run_triage_agent_empty_inbox_no_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty inbox returns [] without invoking the LLM."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        with mock.patch(
            "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider"
        ) as cls:
            out = run_triage_agent(conn)
        assert out == []
        cls.assert_not_called()
    finally:
        conn.close()


def test_run_triage_agent_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Indices map to message_ids; decisions persisted with source='agent'."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        _insert_inbox(conn, "<b@x.com>")
        result_obj = TriageResult(
            items=[
                TriageItem(index=1, action="TO_ANSWER", confidence="high"),
                TriageItem(index=2, action="TO_ARCHIVE", reason="keep"),
            ]
        )
        handle, patcher = _patch_llm(result_obj)
        with patcher:
            out = run_triage_agent(conn)

        assert [(d.message_id, d.action) for d in out] == [
            ("<a@x.com>", "TO_ANSWER"),
            ("<b@x.com>", "TO_ARCHIVE"),
        ]
        # Persisted with source='agent'.
        stored = list_triage_decisions(conn)
        assert all(d.source == "agent" for d in stored)
        assert get_triage_decision(conn, "<a@x.com>").action == "TO_ANSWER"  # type: ignore[union-attr]
        assert get_triage_decision(conn, "<b@x.com>").reason == "keep"  # type: ignore[union-attr]
        handle.close.assert_called_once()
    finally:
        conn.close()


def test_run_triage_agent_uses_cheap_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_agent is called with Tier.CHEAP by default."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        _handle, patcher = _patch_llm(
            TriageResult(items=[TriageItem(index=1, action="TO_ANSWER")])
        )
        with patcher as cls:
            run_triage_agent(conn)
            provider = cls.return_value
        provider.build_agent.assert_called_once()
        assert provider.build_agent.call_args.kwargs["tier"] == Tier.CHEAP
    finally:
        conn.close()


def test_run_triage_agent_clamps_unknown_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown action coerces to user_triage."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        # The model coerces unknown -> user_triage at validation time.
        result_obj = TriageResult(items=[TriageItem(index=1, action="weird")])
        _handle, patcher = _patch_llm(result_obj)
        with patcher:
            out = run_triage_agent(conn)
        assert out[0].action == "HUMAN_TRIAGE"
    finally:
        conn.close()


def test_run_triage_agent_omitted_record_defaults_user_triage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An inbox record the LLM omitted defaults to user_triage."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        _insert_inbox(conn, "<b@x.com>")
        # Only index 1 returned; index 2 omitted.
        result_obj = TriageResult(items=[TriageItem(index=1, action="TO_ANSWER")])
        _handle, patcher = _patch_llm(result_obj)
        with patcher:
            out = run_triage_agent(conn)
        by_id = {d.message_id: d.action for d in out}
        assert by_id == {
            "<a@x.com>": "TO_ANSWER",
            "<b@x.com>": "HUMAN_TRIAGE",
        }
    finally:
        conn.close()


def test_run_triage_agent_only_undecided_skips_decided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """only_undecided=True triages only inbox records with no decision row."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        _insert_inbox(conn, "<b@x.com>")
        # <a@x.com> already has a decision — it must be left untouched.
        set_triage_decision(
            conn, "<a@x.com>", "TO_ARCHIVE", source="user", reason="pre"
        )
        # The LLM sees only the single undecided record at index 1.
        result_obj = TriageResult(items=[TriageItem(index=1, action="TO_ANSWER")])
        _handle, patcher = _patch_llm(result_obj)
        with patcher:
            out = run_triage_agent(conn, only_undecided=True)

        assert [(d.message_id, d.action) for d in out] == [("<b@x.com>", "TO_ANSWER")]
        # The pre-existing decision is unchanged (still user/archive/pre).
        decided = get_triage_decision(conn, "<a@x.com>")
        assert decided is not None
        assert decided.source == "user"
        assert decided.action == "TO_ARCHIVE"
        assert decided.reason == "pre"
    finally:
        conn.close()


def test_run_triage_agent_only_undecided_all_decided_no_llm() -> None:
    """only_undecided=True with every record decided returns [] and no LLM."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        _insert_inbox(conn, "<b@x.com>")
        set_triage_decision(conn, "<a@x.com>", "TO_ARCHIVE", source="user")
        set_triage_decision(conn, "<b@x.com>", "TO_DELETE", source="user")
        with mock.patch(
            "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider"
        ) as cls:
            # No api_key needed: filtering empties the set before any LLM.
            out = run_triage_agent(conn, only_undecided=True)
        assert out == []
        cls.assert_not_called()
    finally:
        conn.close()


def test_run_triage_agent_missing_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """No api_key, no env, no config key → TriageError; LLM not built."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(tmp_path / "missing.yaml"))  # type: ignore[operator]
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        with mock.patch(
            "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider"
        ) as cls:
            with pytest.raises(TriageError) as exc:
                run_triage_agent(conn, api_key=None)
        assert "LLM_API_KEY" in str(exc.value)
        cls.assert_not_called()
    finally:
        conn.close()


def test_run_triage_agent_llm_failure_wrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A call_with_retry failure is wrapped as TriageError; close runs."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        mock_handle = mock.MagicMock()
        mock_provider = mock.MagicMock()
        mock_provider.build_agent.return_value = mock_handle
        mock_handle.run_sync.side_effect = RuntimeError("timeout")
        with mock.patch(
            "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider",
            return_value=mock_provider,
        ):
            with pytest.raises(TriageError) as exc:
                run_triage_agent(conn)
        assert "timeout" in str(exc.value)
        mock_handle.close.assert_called_once()
    finally:
        conn.close()


def test_run_triage_agent_moves_status_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Triage no longer updates mail_records.status — it stays at default."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        _handle, patcher = _patch_llm(
            TriageResult(items=[TriageItem(index=1, action="TO_ARCHIVE")])
        )
        with patcher:
            run_triage_agent(conn)
        # mail_records.status stays at the default "to_read".
        row = conn.execute(
            "SELECT status FROM mail_records WHERE message_id = ?",
            ("<a@x.com>",),
        ).fetchone()
        assert row[0] == "to_read"
    finally:
        conn.close()


def test_run_triage_agent_performs_no_imap_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The triage path performs ZERO IMAP calls."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        _handle, patcher = _patch_llm(
            TriageResult(items=[TriageItem(index=1, action="TO_ARCHIVE")])
        )
        with (
            patcher,
            mock.patch("imaplib.IMAP4") as imap4,
            mock.patch("imaplib.IMAP4_SSL") as imap4_ssl,
        ):
            run_triage_agent(conn)
        # (a) triage decision persisted.
        decision = get_triage_decision(conn, "<a@x.com>")
        assert decision is not None
        assert decision.action == "TO_ARCHIVE"
        # (b) no IMAP constructor was ever called.
        assert imap4.call_count == 0
        assert imap4_ssl.call_count == 0
    finally:
        conn.close()


def test_valid_triage_actions_vocabulary() -> None:
    assert VALID_TRIAGE_ACTIONS == frozenset(
        {
            "INBOX",
            "HUMAN_TRIAGE",
            "PENDING_ACTION",
            "TO_ARCHIVE",
            "TO_DELETE",
            "TO_ANSWER",
            "DRAFT_READY",
        }
    )


def test_build_triage_system_prompt_mentions_canonical_actions() -> None:
    """The LLM system prompt describes the four agent-selectable actions.
    ``INBOX`` (reserved for not-yet-triaged mail) and ``PENDING_ACTION``
    (human-only) are intentionally omitted from the prompt."""
    prompt = _build_triage_system_prompt()
    for action in ("HUMAN_TRIAGE", "TO_ARCHIVE", "TO_DELETE", "TO_ANSWER"):
        assert f"`{action}`" in prompt
    assert "`INBOX`" not in prompt
    assert "`PENDING_ACTION`" not in prompt
    assert "`waiting`" not in prompt
    assert "`ignore`" not in prompt


def test_triage_action_order_is_canonical_columns() -> None:
    """TRIAGE_ACTION_ORDER is exactly the seven canonical columns in display order."""
    assert TRIAGE_ACTION_ORDER == (
        "INBOX",
        "HUMAN_TRIAGE",
        "PENDING_ACTION",
        "TO_ARCHIVE",
        "TO_DELETE",
        "TO_ANSWER",
        "DRAFT_READY",
    )


def test_triage_action_labels_cover_every_action() -> None:
    """TRIAGE_ACTION_LABELS has exactly the 7 canonical keys, each value non-empty."""
    assert set(TRIAGE_ACTION_LABELS.keys()) == set(VALID_TRIAGE_ACTIONS)
    assert len(TRIAGE_ACTION_LABELS) == 7
    for _action, label in TRIAGE_ACTION_LABELS.items():
        assert isinstance(label, str) and len(label) > 0
    assert tuple(TRIAGE_ACTION_LABELS) == TRIAGE_ACTION_ORDER


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
        with patcher:
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


# ---------------------------------------------------------------------------
# Deterministic triage rules — proposal derivation
# ---------------------------------------------------------------------------


def _seed_decision(conn: object, message_id: str, sender: str, action: str) -> None:
    """Insert an inbox record and a triage decision for it."""
    _insert_inbox(conn, message_id, sender=sender)
    set_triage_decision(conn, message_id, action, source="agent")  # type: ignore[arg-type]


def _accept_rule(conn: object, match_type: str, match_value: str, action: str) -> str:
    """Record + accept a rule proposal; return its fingerprint."""
    proposal = TriageRuleProposal(
        match_type=match_type,
        match_value=match_value,
        action=action,
        title="t",
        body="b",
    )
    record_and_filter_rule_proposals(conn, [proposal])  # type: ignore[arg-type]
    fingerprint = _rule_fingerprint(proposal)
    set_rule_state(conn, fingerprint, "accepted")  # type: ignore[arg-type]
    return fingerprint


def test_propose_rules_sender_above_threshold() -> None:
    """A consistent sender at/above threshold yields one sender rule."""
    conn = init_db(":memory:")
    try:
        for i in range(3):
            _seed_decision(conn, f"<m{i}@x.com>", "alice@example.com", "TO_ARCHIVE")
        proposals = propose_triage_rules(conn)
        sender_rules = [p for p in proposals if p.match_type == "sender"]
        assert len(sender_rules) == 1
        assert sender_rules[0].match_value == "alice@example.com"
        assert sender_rules[0].action == "TO_ARCHIVE"
        assert sender_rules[0].confidence in {"low", "medium", "high"}
    finally:
        conn.close()


def test_propose_rules_respects_threshold() -> None:
    """Below the decision threshold no rule is proposed."""
    conn = init_db(":memory:")
    try:
        for i in range(2):
            _seed_decision(conn, f"<m{i}@x.com>", "alice@example.com", "TO_ARCHIVE")
        assert propose_triage_rules(conn) == []
    finally:
        conn.close()


def test_propose_rules_excludes_user_triage() -> None:
    """``HUMAN_TRIAGE`` decisions never drive a rule."""
    conn = init_db(":memory:")
    try:
        for i in range(4):
            _seed_decision(conn, f"<m{i}@x.com>", "alice@example.com", "HUMAN_TRIAGE")
        assert propose_triage_rules(conn) == []
    finally:
        conn.close()


def test_propose_rules_inconsistent_no_rule() -> None:
    """A sender with conflicting actions yields no rule."""
    conn = init_db(":memory:")
    try:
        _seed_decision(conn, "<m0@x.com>", "alice@example.com", "TO_ARCHIVE")
        _seed_decision(conn, "<m1@x.com>", "alice@example.com", "TO_ARCHIVE")
        _seed_decision(conn, "<m2@x.com>", "alice@example.com", "TO_DELETE")
        assert propose_triage_rules(conn) == []
    finally:
        conn.close()


def test_propose_rules_domain_when_multiple_senders() -> None:
    """Two senders in a domain, each below threshold, yield a domain rule."""
    conn = init_db(":memory:")
    try:
        for i in range(2):
            _seed_decision(conn, f"<a{i}@news.com>", "alice@news.com", "TO_ARCHIVE")
        for i in range(2):
            _seed_decision(conn, f"<b{i}@news.com>", "bob@news.com", "TO_ARCHIVE")
        proposals = propose_triage_rules(conn)
        domain_rules = [p for p in proposals if p.match_type == "domain"]
        assert len(domain_rules) == 1
        assert domain_rules[0].match_value == "news.com"
        assert domain_rules[0].action == "TO_ARCHIVE"
        # Neither sender hit the per-sender threshold individually.
        assert [p for p in proposals if p.match_type == "sender"] == []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fingerprint stability
# ---------------------------------------------------------------------------


def test_rule_fingerprint_ignores_case_and_whitespace() -> None:
    """Fingerprint is stable under case / surrounding whitespace."""
    a = TriageRule(
        match_type="sender", match_value="Alice@Example.com", action="TO_ARCHIVE"
    )
    b = TriageRule(
        match_type="sender",
        match_value="  alice@example.com  ",
        action="TO_ARCHIVE",
    )
    assert _rule_fingerprint(a) == _rule_fingerprint(b)


def test_rule_fingerprint_distinct_by_identity_fields() -> None:
    """Differing match_type / match_value / action give distinct fingerprints."""
    base = TriageRule(
        match_type="sender", match_value="alice@example.com", action="TO_ARCHIVE"
    )
    diff_action = TriageRule(
        match_type="sender", match_value="alice@example.com", action="TO_DELETE"
    )
    diff_value = TriageRule(
        match_type="sender", match_value="bob@example.com", action="TO_ARCHIVE"
    )
    diff_type = TriageRule(
        match_type="domain", match_value="alice@example.com", action="TO_ARCHIVE"
    )
    fps = {_rule_fingerprint(r) for r in (base, diff_action, diff_value, diff_type)}
    assert len(fps) == 4


def test_rule_fingerprint_excludes_presentation() -> None:
    """Presentation fields (title/body/confidence) do not affect identity."""
    rule = TriageRule(
        match_type="sender", match_value="alice@example.com", action="TO_ARCHIVE"
    )
    proposal = TriageRuleProposal(
        match_type="sender",
        match_value="alice@example.com",
        action="TO_ARCHIVE",
        title="some title",
        body="some body",
        confidence="high",
    )
    assert _rule_fingerprint(rule) == _rule_fingerprint(proposal)


# ---------------------------------------------------------------------------
# Dedup ledger and state transitions
# ---------------------------------------------------------------------------


def test_record_and_filter_dedup_pending() -> None:
    """A re-proposed (already pending) finding is suppressed."""
    conn = init_db(":memory:")
    try:
        proposal = TriageRuleProposal(
            match_type="sender",
            match_value="alice@example.com",
            action="TO_ARCHIVE",
            title="t",
            body="b",
        )
        assert len(record_and_filter_rule_proposals(conn, [proposal])) == 1
        assert record_and_filter_rule_proposals(conn, [proposal]) == []
    finally:
        conn.close()


def test_record_and_filter_dedup_accepted_and_rejected() -> None:
    """Accepted and rejected findings are also suppressed on re-proposal."""
    conn = init_db(":memory:")
    try:
        accepted = TriageRuleProposal(
            match_type="sender",
            match_value="alice@example.com",
            action="TO_ARCHIVE",
            title="t",
            body="b",
        )
        rejected = TriageRuleProposal(
            match_type="sender",
            match_value="bob@example.com",
            action="TO_DELETE",
            title="t",
            body="b",
        )
        record_and_filter_rule_proposals(conn, [accepted, rejected])
        set_rule_state(conn, _rule_fingerprint(accepted), "accepted")
        set_rule_state(conn, _rule_fingerprint(rejected), "rejected")
        assert record_and_filter_rule_proposals(conn, [accepted, rejected]) == []
    finally:
        conn.close()


def test_set_rule_state_accept_adds_active() -> None:
    """Accepting a proposal adds its rule to the active list and ledger."""
    conn = init_db(":memory:")
    try:
        proposal = TriageRuleProposal(
            match_type="domain",
            match_value="news.com",
            action="TO_ARCHIVE",
            title="t",
            body="b",
        )
        record_and_filter_rule_proposals(conn, [proposal])
        fingerprint = _rule_fingerprint(proposal)
        set_rule_state(conn, fingerprint, "accepted")

        active = _load_active_rules(conn)
        assert len(active) == 1
        assert active[0].match_type == "domain"
        assert active[0].match_value == "news.com"
        assert active[0].action == "TO_ARCHIVE"
        assert _load_rule_ledger(conn)[fingerprint].state == "accepted"
    finally:
        conn.close()


def test_set_rule_state_reject_not_added() -> None:
    """Rejecting a proposal does not add an active rule."""
    conn = init_db(":memory:")
    try:
        proposal = TriageRuleProposal(
            match_type="sender",
            match_value="alice@example.com",
            action="TO_DELETE",
            title="t",
            body="b",
        )
        record_and_filter_rule_proposals(conn, [proposal])
        fingerprint = _rule_fingerprint(proposal)
        set_rule_state(conn, fingerprint, "rejected")
        assert _load_active_rules(conn) == []
        assert _load_rule_ledger(conn)[fingerprint].state == "rejected"
    finally:
        conn.close()


def test_set_rule_state_accept_then_reject_removes_active() -> None:
    """Rejecting a previously-accepted rule removes it from the active set."""
    conn = init_db(":memory:")
    try:
        fingerprint = _accept_rule(conn, "sender", "alice@example.com", "TO_DELETE")
        assert len(_load_active_rules(conn)) == 1
        set_rule_state(conn, fingerprint, "rejected")
        assert _load_active_rules(conn) == []
    finally:
        conn.close()


def test_set_rule_state_unknown_fingerprint_raises() -> None:
    """An unknown fingerprint raises TriageError naming the fingerprint."""
    conn = init_db(":memory:")
    try:
        with pytest.raises(TriageError) as exc:
            set_rule_state(conn, "deadbeef", "accepted")
        assert "deadbeef" in str(exc.value)
    finally:
        conn.close()


def test_set_rule_state_invalid_state_raises() -> None:
    """An invalid state raises TriageError."""
    conn = init_db(":memory:")
    try:
        proposal = TriageRuleProposal(
            match_type="sender",
            match_value="alice@example.com",
            action="TO_DELETE",
            title="t",
            body="b",
        )
        record_and_filter_rule_proposals(conn, [proposal])
        with pytest.raises(TriageError):
            set_rule_state(conn, _rule_fingerprint(proposal), "bogus")
    finally:
        conn.close()


def test_list_rule_proposals_empty_ledger() -> None:
    """An absent/empty ledger yields an empty list."""
    conn = init_db(":memory:")
    try:
        assert list_rule_proposals(conn, "pending") == []
    finally:
        conn.close()


def test_list_rule_proposals_returns_pending_sorted() -> None:
    """Pending entries are returned sorted by (match_type, match_value, action)."""
    conn = init_db(":memory:")
    try:
        proposals = [
            TriageRuleProposal(
                match_type="sender",
                match_value="bob@example.com",
                action="TO_DELETE",
                title="bob",
                body="b",
            ),
            TriageRuleProposal(
                match_type="domain",
                match_value="news.com",
                action="TO_ARCHIVE",
                title="news",
                body="b",
            ),
            TriageRuleProposal(
                match_type="sender",
                match_value="alice@example.com",
                action="TO_ARCHIVE",
                title="alice",
                body="b",
            ),
        ]
        record_and_filter_rule_proposals(conn, proposals)
        result = list_rule_proposals(conn, "pending")
        # Sorted by (match_type, match_value, action).
        assert [entry.match_value for _, entry in result] == [
            "news.com",
            "alice@example.com",
            "bob@example.com",
        ]
        # The fingerprint is the ledger dict key, not recomputed wrongly.
        for fingerprint, entry in result:
            assert (
                _rule_fingerprint(
                    TriageRule(
                        match_type=entry.match_type,
                        match_value=entry.match_value,
                        action=entry.action,
                    )
                )
                == fingerprint
            )
    finally:
        conn.close()


def test_list_rule_proposals_excludes_non_pending() -> None:
    """Accepted/rejected entries are excluded when filtering for pending."""
    conn = init_db(":memory:")
    try:
        pending = TriageRuleProposal(
            match_type="sender",
            match_value="alice@example.com",
            action="TO_ARCHIVE",
            title="alice",
            body="b",
        )
        accepted = TriageRuleProposal(
            match_type="sender",
            match_value="bob@example.com",
            action="TO_DELETE",
            title="bob",
            body="b",
        )
        rejected = TriageRuleProposal(
            match_type="domain",
            match_value="news.com",
            action="TO_ARCHIVE",
            title="news",
            body="b",
        )
        record_and_filter_rule_proposals(conn, [pending, accepted, rejected])
        set_rule_state(conn, _rule_fingerprint(accepted), "accepted")
        set_rule_state(conn, _rule_fingerprint(rejected), "rejected")

        result = list_rule_proposals(conn, "pending")
        assert [entry.match_value for _, entry in result] == ["alice@example.com"]
    finally:
        conn.close()


def test_list_rule_proposals_filters_by_state() -> None:
    """Filtering by a non-pending state returns matching entries."""
    conn = init_db(":memory:")
    try:
        accepted = TriageRuleProposal(
            match_type="sender",
            match_value="bob@example.com",
            action="TO_DELETE",
            title="bob",
            body="b",
        )
        record_and_filter_rule_proposals(conn, [accepted])
        set_rule_state(conn, _rule_fingerprint(accepted), "accepted")

        assert list_rule_proposals(conn, "pending") == []
        accepted_result = list_rule_proposals(conn, "accepted")
        assert [entry.match_value for _, entry in accepted_result] == [
            "bob@example.com"
        ]
    finally:
        conn.close()


def test_list_rule_proposals_invalid_state_raises() -> None:
    """An invalid state raises TriageError."""
    conn = init_db(":memory:")
    try:
        with pytest.raises(TriageError):
            list_rule_proposals(conn, "bogus")
    finally:
        conn.close()


def test_rule_ledger_entry_rejects_invalid_state() -> None:
    """RuleLedgerEntry validates its state field."""
    with pytest.raises(pydantic.ValidationError):
        RuleLedgerEntry(
            match_type="sender",
            match_value="a@b.com",
            action="TO_ARCHIVE",
            state="bogus",
        )


# ---------------------------------------------------------------------------
# apply_triage_rules
# ---------------------------------------------------------------------------


def test_apply_triage_rules_matches_sender() -> None:
    """A sender rule matches by exact lowercased sender."""
    conn = init_db(":memory:")
    try:
        _accept_rule(conn, "sender", "bob@spam.com", "TO_DELETE")
        record = MailRecord(
            message_id="<x>", sender="Bob@Spam.com", subject="hi", date="d"
        )
        assert apply_triage_rules(conn, record) == "TO_DELETE"
    finally:
        conn.close()


def test_apply_triage_rules_matches_domain() -> None:
    """A domain rule matches by the sender's domain."""
    conn = init_db(":memory:")
    try:
        _accept_rule(conn, "domain", "spam.com", "TO_DELETE")
        record = MailRecord(
            message_id="<x>",
            sender="Whoever <anyone@SPAM.com>",
            subject="hi",
            date="d",
        )
        assert apply_triage_rules(conn, record) == "TO_DELETE"
    finally:
        conn.close()


def test_apply_triage_rules_matches_subject_substring() -> None:
    """A subject_contains rule matches a case-insensitive substring."""
    conn = init_db(":memory:")
    try:
        _accept_rule(conn, "subject_contains", "invoice", "TO_ARCHIVE")
        record = MailRecord(
            message_id="<x>",
            sender="a@b.com",
            subject="Your INVOICE is ready",
            date="d",
        )
        assert apply_triage_rules(conn, record) == "TO_ARCHIVE"
    finally:
        conn.close()


def test_apply_triage_rules_no_match_returns_none() -> None:
    """No active rule matching the record returns None."""
    conn = init_db(":memory:")
    try:
        _accept_rule(conn, "sender", "bob@spam.com", "TO_DELETE")
        record = MailRecord(
            message_id="<x>", sender="carol@example.com", subject="hi", date="d"
        )
        assert apply_triage_rules(conn, record) is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# run_triage_agent — deterministic rule fast-path
# ---------------------------------------------------------------------------


def test_run_triage_agent_rule_match_skips_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All inbox mail matched by rules is triaged without an LLM call."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _accept_rule(conn, "sender", "bob@spam.com", "TO_DELETE")
        _insert_inbox(conn, "<bob@spam.com>", sender="bob@spam.com")
        with mock.patch(
            "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider"
        ) as cls:
            out = run_triage_agent(conn)
        assert len(out) == 1
        assert out[0].action == "TO_DELETE"
        assert out[0].reason == "matched deterministic rule"
        cls.assert_not_called()
        # Persisted with source='agent'.
        stored = get_triage_decision(conn, "<bob@spam.com>")
        assert stored is not None
        assert stored.source == "agent"
        assert stored.reason == "matched deterministic rule"
    finally:
        conn.close()


def test_run_triage_agent_only_unmatched_go_to_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule-matched mail is deterministic; only the rest reaches the LLM."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _accept_rule(conn, "sender", "bob@spam.com", "TO_DELETE")
        _insert_inbox(conn, "<bob@spam.com>", sender="bob@spam.com")
        _insert_inbox(conn, "<carol@x.com>", sender="carol@example.com")
        # The LLM only sees the single unmatched record at index 1.
        handle, patcher = _patch_llm(
            TriageResult(items=[TriageItem(index=1, action="TO_ANSWER")])
        )
        with patcher:
            out = run_triage_agent(conn)
        by_id = {d.message_id: d for d in out}
        assert by_id["<bob@spam.com>"].action == "TO_DELETE"
        assert by_id["<bob@spam.com>"].reason == "matched deterministic rule"
        assert by_id["<carol@x.com>"].action == "TO_ANSWER"
        prompt = handle.run_sync.call_args.args[0]
        assert "carol@example.com" in prompt
        assert "bob@spam.com" not in prompt
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# propose_archive_subfolder
# ---------------------------------------------------------------------------


def test_propose_mailing_list_prefix() -> None:
    """[python-dev] → Lists/python-dev."""
    record = _make_record(
        message_id="<a>",
        sender="a@b.com",
        subject="[python-dev] Re: some topic",
        date="2025-06-01T12:00:00",
    )
    assert propose_archive_subfolder(record) == "Lists/python-dev"


def test_propose_mailing_list_with_post_id() -> None:
    """[list:123] → Lists/list."""
    record = _make_record(
        message_id="<a>",
        sender="a@b.com",
        subject="[list:123] Re: something",
        date="2025-06-01T12:00:00",
    )
    assert propose_archive_subfolder(record) == "Lists/list"


def test_propose_mailing_list_with_space_and_id() -> None:
    """[list 456] → Lists/list."""
    record = _make_record(
        message_id="<a>",
        sender="a@b.com",
        subject="[list 456] hello",
        date="2025-06-01T12:00:00",
    )
    assert propose_archive_subfolder(record) == "Lists/list"


def test_propose_empty_brackets_skipped() -> None:
    """[] should fall through to next rule."""
    record = _make_record(
        message_id="<a>",
        sender="alice@example.com",
        subject="[] Re: something",
        date="2025-06-01T12:00:00",
    )
    result = propose_archive_subfolder(record)
    # Falls through to sender rule → example-com/alice
    # (dots are collapsed to dashes by sanitisation)
    assert result == "example-com/alice"


def test_propose_sender_domain_and_local_part() -> None:
    """alice@example.com → example-com/alice."""
    record = _make_record(
        message_id="<a>",
        sender="Alice <alice@example.com>",
        subject="Hello",
        date="2025-06-01T12:00:00",
    )
    assert propose_archive_subfolder(record) == "example-com/alice"


def test_propose_bare_sender_no_brackets() -> None:
    """bob@example.com → example-com/bob."""
    record = _make_record(
        message_id="<a>",
        sender="bob@example.com",
        subject="Hi",
        date="2025-06-01T12:00:00",
    )
    assert propose_archive_subfolder(record) == "example-com/bob"


def test_propose_sender_no_at_falls_through() -> None:
    """Sender with no @ falls through to date."""
    record = _make_record(
        message_id="<a>",
        sender="NoEmailName",
        subject="Hi",
        date="2025-06-15T12:00:00",
    )
    assert propose_archive_subfolder(record) == "2025/06"


def test_propose_date_iso() -> None:
    """ISO date → YYYY/MM."""
    record = _make_record(
        message_id="<a>",
        sender="NoEmail",
        subject="No list",
        date="2025-06-01T12:00:00",
    )
    assert propose_archive_subfolder(record) == "2025/06"


def test_propose_unparseable_date_returns_unknown() -> None:
    """Unparseable date → 'unknown'."""
    record = _make_record(
        message_id="<a>",
        sender="NoEmail",
        subject="No list",
        date="not-a-date",
    )
    assert propose_archive_subfolder(record) == "unknown"


def test_propose_all_rules_fail_returns_empty() -> None:
    """If ALL rules fail (impossible with date fallback? only if date is
    empty and no sender/@domain). Actually date fallback catches empty
    strings too via 'unknown'."""
    record = _make_record(
        message_id="<a>",
        sender="noemail",
        subject="no list",
        date="",
    )
    # Date is empty → unparseable → "unknown"
    assert propose_archive_subfolder(record) == "unknown"


def test_propose_sanitises_special_chars() -> None:
    """Non-alphanumeric chars collapsed to single dash, lowercased."""
    record = _make_record(
        message_id="<a>",
        sender="Alice+Tag <alice+tag@example.com>",
        subject="[My List!!!] Re: topic",
        date="2025-06-01T12:00:00",
    )
    # List rule wins: "My List!!!" → sanitised to "my-list"
    assert propose_archive_subfolder(record) == "Lists/my-list"


# ---------------------------------------------------------------------------
# get_archive_subfolder / set_archive_subfolder_override
# ---------------------------------------------------------------------------


def test_archive_override_round_trip() -> None:
    """Set an override, read it back, clear it, see proposal again."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>", sender="alice@example.com")
        record = _make_record(
            message_id="<a@x.com>",
            sender="alice@example.com",
            subject="Hello",
            date="2025-06-01T12:00:00",
        )
        # Default → deterministic
        default = get_archive_subfolder(conn, "<a@x.com>", record)
        assert default == "example-com/alice"

        # Set override
        set_archive_subfolder_override(conn, "<a@x.com>", "Custom/Folder")
        assert get_archive_subfolder(conn, "<a@x.com>", record) == "Custom/Folder"

        # Clear override (empty string)
        set_archive_subfolder_override(conn, "<a@x.com>", "")
        assert get_archive_subfolder(conn, "<a@x.com>", record) == "example-com/alice"
    finally:
        conn.close()


def test_archive_override_persists_across_connections() -> None:
    """Override written on one connection is visible on another."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn1 = init_db(path)
        _insert_inbox(conn1, "<persist@x.com>")
        set_archive_subfolder_override(conn1, "<persist@x.com>", "MyPath")
        conn1.close()

        conn2 = init_db(path)
        overrides = _load_archive_overrides(conn2)
        assert overrides.get("<persist@x.com>") == "MyPath"
        conn2.close()
    finally:
        os.unlink(path)


def test_archive_llm_hint_priority() -> None:
    """LLM hint is used when no user override exists."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        record = _make_record(
            message_id="<a@x.com>",
            sender="alice@example.com",
            subject="Hello",
            date="2025-06-01T12:00:00",
        )

        # Store an LLM hint
        hints = {"<a@x.com>": "Lists/python-dev"}
        _save_llm_archive_hints(conn, hints)

        # LLM hint takes precedence over deterministic
        assert get_archive_subfolder(conn, "<a@x.com>", record) == "Lists/python-dev"

        # User override takes precedence over LLM hint
        set_archive_subfolder_override(conn, "<a@x.com>", "Custom")
        assert get_archive_subfolder(conn, "<a@x.com>", record) == "Custom"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# _build_triage_system_prompt with archive folders
# ---------------------------------------------------------------------------


def test_system_prompt_with_archive_folders() -> None:
    """When archive_folders is non-empty, the prompt includes folder list."""
    prompt = _build_triage_system_prompt(
        archive_folders=["robotsix-mail-archive", "robotsix-mail-archive/Lists/dev"]
    )
    assert "existing sub-folders" in prompt
    assert "robotsix-mail-archive" in prompt
    assert "robotsix-mail-archive/Lists/dev" in prompt
    assert "archive_subfolder" in prompt
    assert "TO_ARCHIVE" in prompt


def test_system_prompt_without_archive_folders() -> None:
    """When archive_folders is None, prompt is unchanged."""
    prompt = _build_triage_system_prompt(archive_folders=None)
    assert "existing sub-folders" not in prompt
    assert "archive_subfolder" not in prompt


def test_system_prompt_with_empty_archive_folders() -> None:
    """When archive_folders is empty, no archive section is appended."""
    prompt = _build_triage_system_prompt(archive_folders=[])
    assert "existing sub-folders" not in prompt


# ---------------------------------------------------------------------------
# propose_archive_subfolder_llm
# ---------------------------------------------------------------------------


def _patch_llm_for_proposal(
    subfolder: str,
) -> tuple[mock.MagicMock, mock._patch[mock.MagicMock]]:
    """Patch OpenRouterDeepseekProvider to return *subfolder* from the LLM.

    Returns the mock handle (to assert ``close()``) and the patcher.
    """
    from robotsix_auto_mail.triage import ArchiveSubfolderProposal

    mock_run_result = mock.MagicMock()
    mock_run_result.output = ArchiveSubfolderProposal(subfolder=subfolder)
    mock_handle = mock.MagicMock()
    mock_handle.run_sync.return_value = mock_run_result

    mock_provider = mock.MagicMock()
    mock_provider.build_agent.return_value = mock_handle
    mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

    patcher = mock.patch(
        "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider",
        return_value=mock_provider,
    )
    return mock_handle, patcher


def test_propose_archive_subfolder_llm_stores_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful LLM call stores the subfolder in the watermark and
    get_archive_subfolder returns it at priority 2."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<hint-test@x.com>", sender="dev@python.org")
        record = _make_record(
            message_id="<hint-test@x.com>",
            sender="dev@python.org",
            subject="[python-dev] PEP discussion",
            date="2025-06-01T12:00:00",
            body_plain="Let's discuss the new PEP.",
        )

        # Set up archive structure watermark
        set_watermark(
            conn,
            "archive_structure",
            json.dumps(["Lists/python-dev", "Receipts/2025"]),
        )

        handle, patcher = _patch_llm_for_proposal("Lists/python-dev")
        with patcher:
            propose_archive_subfolder_llm(conn, record, api_key="sk-test")

        # Hint stored
        hints = _load_llm_archive_hints(conn)
        assert "<hint-test@x.com>" in hints
        assert hints["<hint-test@x.com>"] == "Lists/python-dev"

        # get_archive_subfolder returns it at priority 2
        result = get_archive_subfolder(conn, "<hint-test@x.com>", record)
        assert result == "Lists/python-dev"

        handle.close.assert_called_once()
    finally:
        conn.close()


def test_propose_archive_subfolder_llm_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing LLM_API_KEY → function returns silently, no hint stored."""
    # Ensure no API key is set
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<nokey@x.com>")
        record = _make_record(
            message_id="<nokey@x.com>",
            sender="alice@example.com",
            subject="Test",
            date="2025-06-01T12:00:00",
        )

        with mock.patch(
            "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider"
        ) as cls:
            propose_archive_subfolder_llm(conn, record, api_key="")

        # LLM never called
        cls.assert_not_called()

        # No hint stored
        hints = _load_llm_archive_hints(conn)
        assert "<nokey@x.com>" not in hints

        # Falls through to deterministic
        result = get_archive_subfolder(conn, "<nokey@x.com>", record)
        assert result == "example-com/alice"
    finally:
        conn.close()


def test_propose_archive_subfolder_llm_llm_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM raises → function returns silently, no hint stored, fallback works."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<error@x.com>")
        record = _make_record(
            message_id="<error@x.com>",
            sender="alice@example.com",
            subject="Test",
            date="2025-06-01T12:00:00",
        )

        mock_provider = mock.MagicMock()
        mock_provider.call_with_retry.side_effect = RuntimeError("LLM down")
        mock_provider.build_agent.return_value = mock.MagicMock()

        with mock.patch(
            "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider",
            return_value=mock_provider,
        ):
            propose_archive_subfolder_llm(conn, record, api_key="sk-test")

        # No hint stored
        hints = _load_llm_archive_hints(conn)
        assert "<error@x.com>" not in hints

        # Falls through to deterministic
        result = get_archive_subfolder(conn, "<error@x.com>", record)
        assert result == "example-com/alice"
    finally:
        conn.close()


def test_propose_archive_subfolder_llm_existing_folders_in_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Archive structure watermark contents appear in the system prompt."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<folders@x.com>")
        record = _make_record(
            message_id="<folders@x.com>",
            sender="dev@python.org",
            subject="PEP 9999",
            date="2025-06-01T12:00:00",
        )

        set_watermark(
            conn,
            "archive_structure",
            json.dumps(["Lists/python-dev", "Receipts/2025"]),
        )

        mock_handle = mock.MagicMock()
        mock_run_result = mock.MagicMock()
        mock_run_result.output = ArchiveSubfolderProposal(subfolder="Lists/python-dev")
        mock_handle.run_sync.return_value = mock_run_result

        mock_provider = mock.MagicMock()
        mock_provider.build_agent.return_value = mock_handle
        mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

        with mock.patch(
            "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider",
            return_value=mock_provider,
        ):
            propose_archive_subfolder_llm(conn, record, api_key="sk-test")

        # Verify system prompt includes the folders
        call_kwargs = mock_provider.build_agent.call_args[1]
        system_prompt = call_kwargs["system_prompt"]
        assert "Lists/python-dev" in system_prompt
        assert "Receipts/2025" in system_prompt
    finally:
        conn.close()


def test_propose_archive_subfolder_llm_sender_memory_in_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When sender has prior decisions, the prompt includes sender guidance."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<memory@x.com>", sender="alice@example.com")
        record = _make_record(
            message_id="<memory@x.com>",
            sender="alice@example.com",
            subject="Monthly report",
            date="2025-06-01T12:00:00",
        )

        # Pre-populate sender memory
        from robotsix_auto_mail.triage import _load_memory, _save_memory, _sender_key

        memory = _load_memory(conn)
        memory[_sender_key("alice@example.com")] = SenderMemory(
            action="TO_ARCHIVE", count=3, last_action="TO_ARCHIVE"
        )
        _save_memory(conn, memory)

        mock_handle = mock.MagicMock()
        mock_run_result = mock.MagicMock()
        mock_run_result.output = ArchiveSubfolderProposal(subfolder="Work/Reports")
        mock_handle.run_sync.return_value = mock_run_result

        mock_provider = mock.MagicMock()
        mock_provider.build_agent.return_value = mock_handle
        mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

        with mock.patch(
            "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider",
            return_value=mock_provider,
        ):
            propose_archive_subfolder_llm(conn, record, api_key="sk-test")

        # Verify system prompt includes sender guidance
        call_kwargs = mock_provider.build_agent.call_args[1]
        system_prompt = call_kwargs["system_prompt"]
        assert "alice@example.com" in system_prompt
        assert "TO_ARCHIVE" in system_prompt
        assert "3 times" in system_prompt
    finally:
        conn.close()


def test_propose_archive_subfolder_llm_proposes_new_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM can return a folder name NOT in the existing archive structure."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<newfolder@x.com>")
        record = _make_record(
            message_id="<newfolder@x.com>",
            sender="news@bbc.co.uk",
            subject="Daily digest",
            date="2025-06-01T12:00:00",
        )

        set_watermark(
            conn,
            "archive_structure",
            json.dumps(["Lists/python-dev"]),
        )

        _handle, patcher = _patch_llm_for_proposal("News/BBC")
        with patcher:
            propose_archive_subfolder_llm(conn, record, api_key="sk-test")

        hints = _load_llm_archive_hints(conn)
        assert hints.get("<newfolder@x.com>") == "News/BBC"
    finally:
        conn.close()


def test_propose_archive_subfolder_llm_empty_subfolder_skips_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM returns empty subfolder → no hint stored (don't pollute watermark)."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<empty@x.com>")
        record = _make_record(
            message_id="<empty@x.com>",
            sender="unknown@nowhere.com",
            subject="???",
            date="2025-06-01T12:00:00",
        )

        _handle, patcher = _patch_llm_for_proposal("")
        with patcher:
            propose_archive_subfolder_llm(conn, record, api_key="sk-test")

        hints = _load_llm_archive_hints(conn)
        assert "<empty@x.com>" not in hints

        # Falls through to deterministic
        result = get_archive_subfolder(conn, "<empty@x.com>", record)
        assert result == "nowhere-com/unknown"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# run_triage_agent stores LLM hints
# ---------------------------------------------------------------------------


def test_run_triage_agent_stores_llm_hints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM returns archive_subfolder for TO_ARCHIVE → hint persisted."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        result_obj = TriageResult(
            items=[
                TriageItem(index=1, action="TO_ARCHIVE", archive_subfolder="Lists/dev")
            ]
        )
        _handle, patcher = _patch_llm(result_obj)
        with patcher:
            run_triage_agent(conn)

        hints = _load_llm_archive_hints(conn)
        assert hints.get("<a@x.com>") == "Lists/dev"
    finally:
        conn.close()


def test_run_triage_agent_clears_stale_hints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A record previously hinted for TO_ARCHIVE is re-triaged to a
    non-archive action → hint removed."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        # Pre-populate an LLM hint for a record that will be re-triaged to
        # a non-TO_ARCHIVE action.
        _insert_inbox(conn, "<a@x.com>")
        _save_llm_archive_hints(conn, {"<a@x.com>": "Lists/old"})

        result_obj = TriageResult(items=[TriageItem(index=1, action="HUMAN_TRIAGE")])
        _handle, patcher = _patch_llm(result_obj)
        with patcher:
            run_triage_agent(conn)

        hints = _load_llm_archive_hints(conn)
        assert "<a@x.com>" not in hints
    finally:
        conn.close()


def test_run_triage_agent_ignores_archive_subfolder_for_non_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM returns archive_subfolder with a non-archive action → hint NOT
    stored."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        result_obj = TriageResult(
            items=[
                TriageItem(
                    index=1,
                    action="HUMAN_TRIAGE",
                    archive_subfolder="should-not-store",
                )
            ]
        )
        _handle, patcher = _patch_llm(result_obj)
        with patcher:
            run_triage_agent(conn)

        hints = _load_llm_archive_hints(conn)
        assert "<a@x.com>" not in hints
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Archive-folder memory
# ---------------------------------------------------------------------------


def test_record_archive_folder_choice_upserts_sender_and_domain() -> None:
    """Recording armada@ls2n.fr → ls2n/armada twice gives the sender entry
    and the ls2n.fr domain entry both count 2 and folder ls2n/armada."""
    conn = init_db(":memory:")
    try:
        record = _make_record(
            message_id="<armada-1@ls2n.fr>",
            sender="armada@ls2n.fr",
            subject="Project update",
            date="2025-06-01T12:00:00",
        )
        record_archive_folder_choice(conn, record, "ls2n/armada")
        record_archive_folder_choice(conn, record, "ls2n/armada")

        memory = _load_archive_folder_memory(conn)

        sender_entry = memory["armada@ls2n.fr"]
        assert sender_entry.subfolder == "ls2n/armada"
        assert sender_entry.count == 2

        domain_entry = memory["ls2n.fr"]
        assert domain_entry.subfolder == "ls2n/armada"
        assert domain_entry.count == 2
    finally:
        conn.close()


def test_record_archive_folder_choice_empty_is_noop() -> None:
    """An empty subfolder records nothing."""
    conn = init_db(":memory:")
    try:
        record = _make_record(
            message_id="<x@ls2n.fr>",
            sender="armada@ls2n.fr",
            subject="x",
            date="2025-06-01T12:00:00",
        )
        record_archive_folder_choice(conn, record, "")
        assert _load_archive_folder_memory(conn) == {}
    finally:
        conn.close()


def test_record_archive_folder_choice_no_at_skips_domain() -> None:
    """A sender with no '@' records only the sender entry, no domain entry."""
    conn = init_db(":memory:")
    try:
        record = _make_record(
            message_id="<noat@x.com>",
            sender="mailer-daemon",
            subject="x",
            date="2025-06-01T12:00:00",
        )
        record_archive_folder_choice(conn, record, "System/Bounces")
        memory = _load_archive_folder_memory(conn)
        assert memory["mailer-daemon"].subfolder == "System/Bounces"
        assert list(memory) == ["mailer-daemon"]
    finally:
        conn.close()


def test_propose_archive_subfolder_llm_folder_memory_in_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When archive-folder memory has an entry for the sender or its domain,
    the system prompt names the previously-used folder and instructs the
    model to prefer reusing an existing project folder."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<armada@ls2n.fr>", sender="crew@ls2n-fr.org")
        record = _make_record(
            message_id="<armada@ls2n.fr>",
            sender="crew@ls2n-fr.org",
            subject="Armada milestone",
            date="2025-06-01T12:00:00",
        )

        # Domain history only (a *similar* sender at the same domain).
        from robotsix_auto_mail.triage import _save_archive_folder_memory

        _save_archive_folder_memory(
            conn,
            {"ls2n-fr.org": ArchiveFolderMemory(subfolder="ls2n/armada", count=3)},
        )

        mock_handle = mock.MagicMock()
        mock_run_result = mock.MagicMock()
        mock_run_result.output = ArchiveSubfolderProposal(subfolder="ls2n/armada")
        mock_handle.run_sync.return_value = mock_run_result

        mock_provider = mock.MagicMock()
        mock_provider.build_agent.return_value = mock_handle
        mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

        with mock.patch(
            "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider",
            return_value=mock_provider,
        ):
            propose_archive_subfolder_llm(conn, record, api_key="sk-test")

        system_prompt = mock_provider.build_agent.call_args[1]["system_prompt"]
        assert "ls2n/armada" in system_prompt
        assert "domain `ls2n-fr.org`" in system_prompt
        assert "prefer reusing an existing project folder" in system_prompt.lower()
    finally:
        conn.close()


def test_system_prompt_with_archive_folder_history() -> None:
    """When archive_folder_history is given, the TO_ARCHIVE paragraph names
    the previously-used folder and the prefer-reuse instruction."""
    prompt = _build_triage_system_prompt(
        archive_folders=["ls2n/armada"],
        archive_folder_history=[
            "- Mail from `armada@ls2n.fr` was previously archived to `ls2n/armada`."
        ],
    )
    assert "ls2n/armada" in prompt
    assert "armada@ls2n.fr" in prompt
    assert "prefer reusing an existing project folder" in prompt.lower()


def test_system_prompt_without_archive_folder_history() -> None:
    """With no history the prompt is byte-for-byte identical to the
    archive-folders-only prompt (additive change)."""
    base = _build_triage_system_prompt(archive_folders=["ls2n/armada"])
    with_none = _build_triage_system_prompt(
        archive_folders=["ls2n/armada"], archive_folder_history=None
    )
    with_empty = _build_triage_system_prompt(
        archive_folders=["ls2n/armada"], archive_folder_history=[]
    )
    assert with_none == base
    assert with_empty == base
    assert "Archive-folder history" not in base


def test_run_triage_agent_does_not_populate_archive_folder_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running the triage agent and storing an LLM archive hint does NOT by
    itself populate archive-folder memory (only human-confirmed paths do)."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        result_obj = TriageResult(
            items=[
                TriageItem(index=1, action="TO_ARCHIVE", archive_subfolder="Lists/dev")
            ]
        )
        _handle, patcher = _patch_llm(result_obj)
        with patcher:
            run_triage_agent(conn)

        # LLM hint stored, but archive-folder memory untouched.
        assert _load_llm_archive_hints(conn).get("<a@x.com>") == "Lists/dev"
        assert _load_archive_folder_memory(conn) == {}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# UnsubscribeDetection model
# ---------------------------------------------------------------------------


def test_unsubscribe_detection_defaults() -> None:
    """method defaults to '', url to '', description to '', confidence to 'medium'."""
    from robotsix_auto_mail.triage import UnsubscribeDetection

    d = UnsubscribeDetection(has_unsubscribe=True)
    assert d.has_unsubscribe is True
    assert d.method == ""
    assert d.url == ""
    assert d.description == ""
    assert d.confidence == "medium"


def test_unsubscribe_detection_requires_has_unsubscribe() -> None:
    """has_unsubscribe is a required field."""
    from robotsix_auto_mail.triage import UnsubscribeDetection

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
    from robotsix_auto_mail.triage import _detect_unsubscribe_for_sender

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
    with mock.patch(
        "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider"
    ) as cls:
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
    from robotsix_auto_mail.triage import _detect_unsubscribe_for_sender

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
    with mock.patch(
        "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider"
    ) as cls:
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
    from robotsix_auto_mail.triage import (
        UnsubscribeDetection,
        _detect_unsubscribe_for_sender,
    )

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
        "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider",
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
    from robotsix_auto_mail.triage import _detect_unsubscribe_for_sender

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
        "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider",
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
    from robotsix_auto_mail.triage import (
        _UNSUBSCRIBE_SUGGESTIONS_KEY,
        _check_unsubscribe_for_to_delete,
    )

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
    from robotsix_auto_mail.triage import (
        _UNSUBSCRIBE_SUGGESTIONS_KEY,
        _check_unsubscribe_for_to_delete,
    )

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

        with mock.patch(
            "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider"
        ) as cls:
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
    from robotsix_auto_mail.triage import (
        _UNSUBSCRIBE_SUGGESTIONS_KEY,
        _check_unsubscribe_for_to_delete,
    )

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

        with mock.patch(
            "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider"
        ) as cls:
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
    from robotsix_auto_mail.triage import (
        _UNSUBSCRIBE_SUGGESTIONS_KEY,
        _check_unsubscribe_for_to_delete,
    )

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
