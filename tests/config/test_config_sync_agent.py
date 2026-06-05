"""Tests for the optional LLM-driven config-drift advisory agent.

These exercise ``src/robotsix_auto_mail/config_sync.py`` — distinct from
``tests/config/test_config_sync.py``, which tests the deterministic
``scripts/config/check_config_sync.py`` gate.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pydantic
import pytest
from robotsix_llmio.core import Tier

from robotsix_auto_mail.config_sync import (
    ConfigSyncError,
    ConfigSyncResult,
    DriftProposal,
    run_config_sync_agent,
)

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
        "robotsix_auto_mail.config_sync.OpenRouterDeepseekProvider",
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
        "robotsix_auto_mail.config_sync.OpenRouterDeepseekProvider"
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
    mock_provider.call_with_retry.side_effect = RuntimeError("timeout")
    with mock.patch(
        "robotsix_auto_mail.config_sync.OpenRouterDeepseekProvider",
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
