"""Tests for run_triage_agent storing LLM archive-subfolder hints."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.db import (
    MailRecord,
    init_db,
    insert_record,
)
from robotsix_auto_mail.triage import (
    ArchiveSubfolderProposal,
    TriageItem,
    TriageResult,
    _load_llm_archive_hints,
    _save_llm_archive_hints,
    propose_archive_subfolder_llm,
    run_triage_agent,
)
from tests.conftest import _make_record


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
# run_triage_agent stores LLM hints
# ---------------------------------------------------------------------------


def test_run_triage_agent_stores_llm_hints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM returns archive_subfolder for TO_ARCHIVE → hint persisted."""
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
            run_triage_agent(conn, api_key="sk-test")

        hints = _load_llm_archive_hints(conn)
        assert hints.get("<a@x.com>") == "Lists/dev"
    finally:
        conn.close()


def test_run_triage_agent_clears_stale_hints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A record previously hinted for TO_ARCHIVE is re-triaged to a
    non-archive action → hint removed."""
    conn = init_db(":memory:")
    try:
        # Pre-populate an LLM hint for a record that will be re-triaged to
        # a non-TO_ARCHIVE action.
        _insert_inbox(conn, "<a@x.com>")
        _save_llm_archive_hints(conn, {"<a@x.com>": "Lists/old"})

        result_obj = TriageResult(items=[TriageItem(index=1, action="HUMAN_TRIAGE")])
        _handle, patcher = _patch_llm(result_obj)
        with patcher:
            run_triage_agent(conn, api_key="sk-test")

        hints = _load_llm_archive_hints(conn)
        assert "<a@x.com>" not in hints
    finally:
        conn.close()


def test_run_triage_agent_ignores_archive_subfolder_for_non_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM returns archive_subfolder with a non-archive action → hint NOT
    stored."""
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
            run_triage_agent(conn, api_key="sk-test")

        hints = _load_llm_archive_hints(conn)
        assert "<a@x.com>" not in hints
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# propose_archive_subfolder_llm — triage-rules injection
# ---------------------------------------------------------------------------


def test_propose_archive_subfolder_llm_rules_in_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a non-empty ``rules`` text is passed, the system prompt injects
    the user's triage rules verbatim."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<armada@ls2n.fr>", sender="crew@ls2n-fr.org")
        record = _make_record(
            message_id="<armada@ls2n.fr>",
            sender="crew@ls2n-fr.org",
            subject="Armada milestone",
            date="2025-06-01T12:00:00",
        )

        rules = "- Mail from the Armada project goes to `ls2n/armada`."

        mock_handle = mock.MagicMock()
        mock_run_result = mock.MagicMock()
        mock_run_result.output = ArchiveSubfolderProposal(subfolder="ls2n/armada")
        mock_handle.run_sync.return_value = mock_run_result

        mock_provider = mock.MagicMock()
        mock_provider.build_agent.return_value = mock_handle
        mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

        with mock.patch(
            "robotsix_llmio.core.factory.get_provider_for_identifier",
            return_value=mock_provider,
        ):
            propose_archive_subfolder_llm(conn, record, api_key="sk-test", rules=rules)

        system_prompt = mock_provider.build_agent.call_args[1]["system_prompt"]
        assert "The user's triage rules" in system_prompt
        assert rules in system_prompt
    finally:
        conn.close()


def test_propose_archive_subfolder_llm_no_rules_omits_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With empty ``rules`` the triage-rules section is absent from the
    system prompt."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<armada@ls2n.fr>", sender="crew@ls2n-fr.org")
        record = _make_record(
            message_id="<armada@ls2n.fr>",
            sender="crew@ls2n-fr.org",
            subject="Armada milestone",
            date="2025-06-01T12:00:00",
        )

        mock_handle = mock.MagicMock()
        mock_run_result = mock.MagicMock()
        mock_run_result.output = ArchiveSubfolderProposal(subfolder="ls2n/armada")
        mock_handle.run_sync.return_value = mock_run_result

        mock_provider = mock.MagicMock()
        mock_provider.build_agent.return_value = mock_handle
        mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

        with mock.patch(
            "robotsix_llmio.core.factory.get_provider_for_identifier",
            return_value=mock_provider,
        ):
            propose_archive_subfolder_llm(conn, record, api_key="sk-test")

        system_prompt = mock_provider.build_agent.call_args[1]["system_prompt"]
        assert "The user's triage rules" not in system_prompt
    finally:
        conn.close()
