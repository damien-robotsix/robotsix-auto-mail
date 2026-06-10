"""Tests for the optional LLM-driven config-drift advisory agent.

These exercise ``src/robotsix_auto_mail/config/config_sync_agent.py`` — distinct from
``tests/config/test_config_sync.py``, which tests the deterministic
``scripts/config/check_config_sync.py`` gate.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pydantic
import pytest
from robotsix_llmio.core import Tier

from robotsix_auto_mail.config.config_sync_agent import (
    ConfigSyncError,
    ConfigSyncResult,
    DriftProposal,
    LedgerEntry,
    _proposal_fingerprint,
    record_and_filter_proposals,
    run_config_sync_agent,
    set_finding_state,
)
from robotsix_auto_mail.db import init_db

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _patch_llm(
    result_obj: ConfigSyncResult,
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
        "robotsix_auto_mail.config.config_sync_agent.OpenRouterDeepseekProvider",
        return_value=mock_provider,
    )
    return mock_handle, patcher


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


def test_drift_proposal_defaults() -> None:
    """affected_field defaults to "" and confidence to "medium"."""
    proposal = DriftProposal(title="t", body="b")
    assert proposal.affected_field == ""
    assert proposal.confidence == "medium"


def test_config_sync_result_defaults_empty() -> None:
    """proposals defaults to an empty list (the no-drift outcome)."""
    assert ConfigSyncResult().proposals == []


def test_drift_proposal_rejects_unknown_confidence() -> None:
    """An out-of-set confidence raises a pydantic ValidationError."""
    with pytest.raises(pydantic.ValidationError):
        DriftProposal(title="t", body="b", confidence="bogus")


def test_config_sync_error_is_exception() -> None:
    err = ConfigSyncError("boom")
    assert isinstance(err, Exception)
    assert str(err) == "boom"


# ---------------------------------------------------------------------------
# run_config_sync_agent
# ---------------------------------------------------------------------------


def test_run_config_sync_agent_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ConfigSyncResult with proposals round-trips; handle is closed."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    result_obj = ConfigSyncResult(
        proposals=[
            DriftProposal(
                title="imap_folder default mismatch",
                body="Docs say INBOX.All but the dataclass default is INBOX.",
                affected_field="imap_folder",
                confidence="high",
            )
        ]
    )
    handle, patcher = _patch_llm(result_obj)
    with patcher:
        out = run_config_sync_agent()

    assert isinstance(out, ConfigSyncResult)
    assert len(out.proposals) == 1
    proposal = out.proposals[0]
    assert proposal.title == "imap_folder default mismatch"
    assert proposal.body.startswith("Docs say")
    assert proposal.affected_field == "imap_folder"
    assert proposal.confidence == "high"
    handle.close.assert_called_once()


def test_run_config_sync_agent_uses_cheap_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_agent is called with Tier.CHEAP by default."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    _handle, patcher = _patch_llm(ConfigSyncResult(proposals=[]))
    with patcher as cls:
        run_config_sync_agent()
        provider = cls.return_value
    provider.build_agent.assert_called_once()
    assert provider.build_agent.call_args.kwargs["tier"] == Tier.CHEAP


def test_run_config_sync_agent_empty_no_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty proposals list is returned unchanged as 'no drift'."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    handle, patcher = _patch_llm(ConfigSyncResult(proposals=[]))
    with patcher:
        out = run_config_sync_agent()
    assert out.proposals == []
    handle.close.assert_called_once()


def test_run_config_sync_agent_missing_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No api_key, no LLM_API_KEY env, no config key → ConfigSyncError."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    # Point the config loader at a non-existent file so no key is resolved.
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(tmp_path / "missing.yaml"))
    with mock.patch(
        "robotsix_auto_mail.config.config_sync_agent.OpenRouterDeepseekProvider"
    ) as cls:
        with pytest.raises(ConfigSyncError) as exc:
            run_config_sync_agent(api_key=None)
    assert "LLM_API_KEY" in str(exc.value)
    cls.assert_not_called()


def test_run_config_sync_agent_llm_failure_wrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A call_with_retry failure is wrapped as ConfigSyncError; close runs."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    mock_handle = mock.MagicMock()
    mock_provider = mock.MagicMock()
    mock_provider.build_agent.return_value = mock_handle
    mock_handle.run_sync.side_effect = RuntimeError("timeout")
    with mock.patch(
        "robotsix_auto_mail.config.config_sync_agent.OpenRouterDeepseekProvider",
        return_value=mock_provider,
    ):
        with pytest.raises(ConfigSyncError) as exc:
            run_config_sync_agent()
    assert "timeout" in str(exc.value)
    mock_handle.close.assert_called_once()


def test_run_config_sync_agent_all_surfaces_reach_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All four surfaces + the ground-truth mappings reach the user message."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    handle, patcher = _patch_llm(ConfigSyncResult(proposals=[]))
    with patcher:
        run_config_sync_agent()

    user_message = handle.run_sync.call_args.args[0]

    # The three on-disk surfaces are embedded verbatim.
    yaml_text = (_REPO_ROOT / "config" / "mail.local.example.yaml").read_text()
    env_text = (_REPO_ROOT / ".env.example").read_text()
    docs_text = (_REPO_ROOT / "docs" / "connecting.md").read_text()
    assert yaml_text in user_message
    assert env_text in user_message
    assert docs_text in user_message

    # The MailConfig dataclass surface and the mappings are embedded too.
    assert "MailConfig fields" in user_message
    assert "imap_host" in user_message
    assert "imap_host: yaml=`imap.host`" in user_message


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------


def test_fingerprint_stable_across_body_wording() -> None:
    """Fingerprint ignores body wording — same field+title => same id."""
    a = DriftProposal(
        title="imap_folder default mismatch",
        body="Docs say INBOX.All but the dataclass default is INBOX.",
        affected_field="imap_folder",
    )
    b = DriftProposal(
        title="imap_folder default mismatch",
        body="The documentation claims INBOX.All; the default is INBOX.",
        affected_field="imap_folder",
    )
    assert _proposal_fingerprint(a) == _proposal_fingerprint(b)


def test_fingerprint_ignores_case_and_whitespace() -> None:
    """Fingerprint normalises case and surrounding whitespace."""
    a = DriftProposal(title="Default Mismatch", body="x", affected_field="imap_folder")
    b = DriftProposal(
        title="  default mismatch  ", body="y", affected_field=" imap_folder "
    )
    assert _proposal_fingerprint(a) == _proposal_fingerprint(b)


def test_fingerprint_distinct_for_different_findings() -> None:
    """Different title or affected_field => different fingerprint."""
    base = DriftProposal(title="t", body="b", affected_field="imap_folder")
    other_title = DriftProposal(title="other", body="b", affected_field="imap_folder")
    other_field = DriftProposal(title="t", body="b", affected_field="db_path")
    assert _proposal_fingerprint(base) != _proposal_fingerprint(other_title)
    assert _proposal_fingerprint(base) != _proposal_fingerprint(other_field)


# ---------------------------------------------------------------------------
# LedgerEntry
# ---------------------------------------------------------------------------


def test_ledger_entry_rejects_unknown_state() -> None:
    """An out-of-set state raises a pydantic ValidationError."""
    with pytest.raises(pydantic.ValidationError):
        LedgerEntry(title="t", state="bogus")


# ---------------------------------------------------------------------------
# Dedup ledger: record_and_filter_proposals
# ---------------------------------------------------------------------------


def _drift(title: str, field: str = "f", body: str = "b") -> DriftProposal:
    return DriftProposal(title=title, body=body, affected_field=field)


def test_record_and_filter_first_run_returns_all() -> None:
    """A fresh ledger returns all proposals and records them as pending."""
    conn = init_db(":memory:")
    try:
        proposals = [_drift("a"), _drift("b")]
        out = record_and_filter_proposals(conn, proposals)
        assert [p.title for p in out] == ["a", "b"]
        # Both are remembered as pending.
        for p in proposals:
            fp = _proposal_fingerprint(p)
            assert fp is not None
        second = record_and_filter_proposals(conn, proposals)
        assert second == []
    finally:
        conn.close()


def test_record_and_filter_dedups_reworded_body() -> None:
    """A second run with the same finding (reworded body) returns []."""
    conn = init_db(":memory:")
    try:
        first = record_and_filter_proposals(
            conn, [_drift("dup", body="original wording")]
        )
        assert len(first) == 1
        second = record_and_filter_proposals(
            conn, [_drift("dup", body="completely different wording")]
        )
        assert second == []
    finally:
        conn.close()


def test_record_and_filter_new_finding_passes_while_old_filtered() -> None:
    """A genuinely-new finding is returned; previously-seen ones filtered."""
    conn = init_db(":memory:")
    try:
        record_and_filter_proposals(conn, [_drift("old")])
        out = record_and_filter_proposals(conn, [_drift("old"), _drift("new")])
        assert [p.title for p in out] == ["new"]
    finally:
        conn.close()


def test_set_finding_state_suppresses_reproposal() -> None:
    """A finding marked accepted is not re-proposed on a later run."""
    conn = init_db(":memory:")
    try:
        proposal = _drift("accept-me")
        record_and_filter_proposals(conn, [proposal])
        set_finding_state(conn, _proposal_fingerprint(proposal), "accepted")
        out = record_and_filter_proposals(conn, [proposal])
        assert out == []
    finally:
        conn.close()


def test_set_finding_state_rejects_invalid_state() -> None:
    """An invalid target state raises ConfigSyncError."""
    conn = init_db(":memory:")
    try:
        proposal = _drift("x")
        record_and_filter_proposals(conn, [proposal])
        with pytest.raises(ConfigSyncError):
            set_finding_state(conn, _proposal_fingerprint(proposal), "bogus")
    finally:
        conn.close()


def test_set_finding_state_unknown_fingerprint_raises() -> None:
    """An unknown fingerprint raises ConfigSyncError."""
    conn = init_db(":memory:")
    try:
        with pytest.raises(ConfigSyncError):
            set_finding_state(conn, "deadbeefdeadbeef", "accepted")
    finally:
        conn.close()


def test_ledger_persists_across_connections() -> None:
    """The ledger written on one connection is visible on another."""
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn1 = init_db(path)
        record_and_filter_proposals(conn1, [_drift("persisted")])
        conn1.close()

        conn2 = init_db(path)
        out = record_and_filter_proposals(conn2, [_drift("persisted")])
        assert out == []
        conn2.close()
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# run_config_sync_agent dedup integration
# ---------------------------------------------------------------------------


def test_run_config_sync_agent_without_conn_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without conn the LLM proposals are returned unchanged."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    result_obj = ConfigSyncResult(proposals=[_drift("only")])
    handle, patcher = _patch_llm(result_obj)
    with patcher:
        out = run_config_sync_agent()
    assert [p.title for p in out.proposals] == ["only"]
    handle.close.assert_called_once()


def test_run_config_sync_agent_with_conn_dedups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With conn, the same finding is filtered on the second run."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        result_obj = ConfigSyncResult(proposals=[_drift("recurring")])
        handle, patcher = _patch_llm(result_obj)
        with patcher:
            first = run_config_sync_agent(conn=conn)
            assert [p.title for p in first.proposals] == ["recurring"]
            second = run_config_sync_agent(conn=conn)
            assert second.proposals == []
        handle.close.assert_called()
    finally:
        conn.close()
