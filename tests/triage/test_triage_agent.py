"""Tests for run_triage_agent."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.db import (
    MailRecord,
    init_db,
    insert_record,
)
from robotsix_auto_mail.triage import (
    TRIAGE_ACTION_LABELS,
    TRIAGE_ACTION_ORDER,
    VALID_TRIAGE_ACTIONS,
    TriageError,
    TriageItem,
    TriageResult,
    _build_triage_system_prompt,
    get_triage_decision,
    list_triage_decisions,
    run_triage_agent,
    set_triage_decision,
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
        "robotsix_llmio.core.factory.get_provider_for_identifier",
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
# run_triage_agent
# ---------------------------------------------------------------------------


def test_run_triage_agent_empty_inbox_no_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty inbox returns [] without invoking the LLM."""
    conn = init_db(":memory:")
    try:
        with mock.patch(
            "robotsix_llmio.core.factory.get_provider_for_identifier"
        ) as cls:
            out = run_triage_agent(conn, api_key="sk-test")
        assert out == []
        cls.assert_not_called()
    finally:
        conn.close()


def test_run_triage_agent_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Indices map to message_ids; decisions persisted with source='agent'."""
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
        with (
            patcher,
            mock.patch("robotsix_auto_mail.triage.agent.propose_archive_subfolder_llm"),
        ):
            out = run_triage_agent(conn, api_key="sk-test")

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
    """build_agent is called with level=1 (cheap) by default."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        _handle, patcher = _patch_llm(
            TriageResult(items=[TriageItem(index=1, action="TO_ANSWER")])
        )
        with patcher as cls:
            run_triage_agent(conn, api_key="sk-test")
            provider = cls.return_value
        provider.build_agent.assert_called_once()
        assert provider.build_agent.call_args.kwargs["level"] == 1
    finally:
        conn.close()


def test_run_triage_agent_forwards_user_email_to_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When user_email is passed, the built agent's system prompt includes it."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        _handle, patcher = _patch_llm(
            TriageResult(items=[TriageItem(index=1, action="TO_ARCHIVE")])
        )
        with (
            patcher as cls,
            mock.patch("robotsix_auto_mail.triage.agent.propose_archive_subfolder_llm"),
        ):
            run_triage_agent(conn, api_key="sk-test", user_email="me@example.com")
            provider = cls.return_value
        call_kwargs = provider.build_agent.call_args.kwargs
        assert "me@example.com" in call_kwargs["system_prompt"]
    finally:
        conn.close()


def test_run_triage_agent_clamps_unknown_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown action coerces to user_triage."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        # The model coerces unknown -> user_triage at validation time.
        result_obj = TriageResult(items=[TriageItem(index=1, action="weird")])
        _handle, patcher = _patch_llm(result_obj)
        with patcher:
            out = run_triage_agent(conn, api_key="sk-test")
        assert out[0].action == "HUMAN_TRIAGE"
    finally:
        conn.close()


def test_run_triage_agent_omitted_record_defaults_user_triage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An inbox record the LLM omitted defaults to user_triage."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        _insert_inbox(conn, "<b@x.com>")
        # Only index 1 returned; index 2 omitted.
        result_obj = TriageResult(items=[TriageItem(index=1, action="TO_ANSWER")])
        _handle, patcher = _patch_llm(result_obj)
        with patcher:
            out = run_triage_agent(conn, api_key="sk-test")
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
            out = run_triage_agent(conn, api_key="sk-test", only_undecided=True)

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
            "robotsix_llmio.core.factory.get_provider_for_identifier"
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
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(tmp_path / "missing.yaml"))  # type: ignore[operator]
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        with mock.patch(
            "robotsix_llmio.core.factory.get_provider_for_identifier"
        ) as cls:
            with pytest.raises(TriageError) as exc:
                run_triage_agent(conn, api_key=None)
        assert "llm_api_key" in str(exc.value)
        cls.assert_not_called()
    finally:
        conn.close()


def test_run_triage_agent_llm_failure_wrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A call_with_retry failure is wrapped as TriageError; close runs."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        mock_handle = mock.MagicMock()
        mock_provider = mock.MagicMock()
        mock_provider.build_agent.return_value = mock_handle
        mock_handle.run_sync.side_effect = RuntimeError("timeout")
        with mock.patch(
            "robotsix_llmio.core.factory.get_provider_for_identifier",
            return_value=mock_provider,
        ):
            with pytest.raises(TriageError) as exc:
                run_triage_agent(conn, api_key="sk-test")
        assert "timeout" in str(exc.value)
        mock_handle.close.assert_called_once()
    finally:
        conn.close()


def test_run_triage_agent_moves_status_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Triage no longer updates mail_records.status — it stays at default."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        _handle, patcher = _patch_llm(
            TriageResult(items=[TriageItem(index=1, action="TO_ARCHIVE")])
        )
        with patcher:
            run_triage_agent(conn, api_key="sk-test")
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
            run_triage_agent(conn, api_key="sk-test")
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
            "TO_CALENDAR",
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
    """TRIAGE_ACTION_ORDER is exactly the eight canonical columns in display order."""
    assert TRIAGE_ACTION_ORDER == (
        "INBOX",
        "HUMAN_TRIAGE",
        "PENDING_ACTION",
        "TO_ARCHIVE",
        "TO_DELETE",
        "TO_CALENDAR",
        "TO_ANSWER",
        "DRAFT_READY",
    )


def test_triage_action_labels_cover_every_action() -> None:
    """TRIAGE_ACTION_LABELS has exactly the 8 canonical keys, each value non-empty."""
    assert set(TRIAGE_ACTION_LABELS.keys()) == set(VALID_TRIAGE_ACTIONS)
    assert len(TRIAGE_ACTION_LABELS) == 8
    for _action, label in TRIAGE_ACTION_LABELS.items():
        assert isinstance(label, str)
        assert len(label) > 0
    assert tuple(TRIAGE_ACTION_LABELS) == TRIAGE_ACTION_ORDER
