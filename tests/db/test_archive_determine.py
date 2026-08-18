"""Tests for archive structure determination and prompt content."""

from unittest import mock

import pytest

from robotsix_auto_mail.core._constants import _ARCHIVE_TAXONOMY_GUIDANCE
from robotsix_auto_mail.db.archive import (
    ArchiveError,
    ArchiveStructure,
    _build_archive_system_prompt,
    determine_archive_structure,
)
from tests.db.conftest import _patch_llm


# ---------------------------------------------------------------------------
# determine_archive_structure
# ---------------------------------------------------------------------------


def test_determine_archive_structure_success() -> None:
    """The model's relative sub-paths are returned."""
    with _patch_llm(["Receipts", "Work/2024"]):
        result = determine_archive_structure(["INBOX", "Sent"], api_key="sk-test")
    assert result == ["Receipts", "Work/2024"]


def test_determine_archive_structure_uses_cheap_tier() -> None:
    """build_agent is called with level=1 (cheap) by default."""
    with mock.patch("robotsix_llmio.core.factory.get_provider_for_identifier") as cls:
        mock_run_result = mock.MagicMock()
        mock_run_result.output = ArchiveStructure(folders=[])
        mock_handle = mock.MagicMock()
        mock_handle.run_sync.return_value = mock_run_result
        provider = cls.return_value
        provider.build_agent.return_value = mock_handle
        provider.call_with_retry.side_effect = lambda fn, what: fn()

        determine_archive_structure(["INBOX"], api_key="sk-test")

    provider.build_agent.assert_called_once()
    assert provider.build_agent.call_args.kwargs["level"] == 1
    mock_handle.close.assert_called_once()


def test_determine_archive_structure_missing_api_key() -> None:
    """No api_key and no LLM_API_KEY env var → ArchiveError."""
    with pytest.raises(ArchiveError) as exc:
        determine_archive_structure(["INBOX"])
    assert "openrouter.keys" in str(exc.value)


def test_determine_archive_structure_llm_error_wrapped() -> None:
    """A call_with_retry failure is wrapped in ArchiveError."""
    mock_handle = mock.MagicMock()
    mock_provider = mock.MagicMock()
    mock_provider.build_agent.return_value = mock_handle
    mock_handle.run_sync.side_effect = RuntimeError("timeout")
    with mock.patch(
        "robotsix_llmio.core.factory.get_provider_for_identifier",
        return_value=mock_provider,
    ):
        with pytest.raises(ArchiveError) as exc:
            determine_archive_structure(["INBOX"], api_key="sk-test")
    assert "timeout" in str(exc.value)
    mock_handle.close.assert_called_once()


# ---------------------------------------------------------------------------
# Prompt content — taxonomy guidance
# ---------------------------------------------------------------------------


def test_archive_structure_prompt_includes_taxonomy_guidance() -> None:
    """The structure-proposal prompt includes the shared taxonomy guidance."""
    prompt = _build_archive_system_prompt("robotsix-mail-archive")
    lower = prompt.lower()
    assert "purpose" in lower
    assert "topic" in lower
    assert "do not use bare" in lower
    assert "domain" in lower
    assert "sender" in lower
    assert "at most 2 levels" in prompt


def test_archive_structure_prompt_legacy_folders_guidance() -> None:
    """The structure prompt warns against propagating legacy domain/sender patterns."""
    prompt = _build_archive_system_prompt("robotsix-mail-archive")
    assert "legacy" in prompt.lower()
    assert "re-home" in prompt.lower() or "do not propagate" in prompt.lower()


def test_archive_and_triage_prompts_share_taxonomy() -> None:
    """Both prompts embed the exact same _ARCHIVE_TAXONOMY_GUIDANCE string."""
    from robotsix_auto_mail.triage import _build_triage_system_prompt

    archive_prompt = _build_archive_system_prompt("root")
    triage_prompt = _build_triage_system_prompt(archive_folders=["Newsletters/LWN"])
    assert _ARCHIVE_TAXONOMY_GUIDANCE in archive_prompt
    assert _ARCHIVE_TAXONOMY_GUIDANCE in triage_prompt
