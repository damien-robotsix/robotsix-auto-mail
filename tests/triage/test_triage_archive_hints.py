"""Tests for run_triage_agent storing LLM hints and archive-folder memory."""

from __future__ import annotations

from unittest import mock

import pytest
from tests.conftest import _make_record

from robotsix_auto_mail.db import (
    MailRecord,
    init_db,
    insert_record,
)
from robotsix_auto_mail.triage import (
    ArchiveFolderMemory,
    ArchiveSubfolderProposal,
    TriageItem,
    TriageResult,
    _build_triage_system_prompt,
    _load_archive_folder_memory,
    _load_llm_archive_hints,
    _save_llm_archive_hints,
    propose_archive_subfolder_llm,
    record_archive_folder_choice,
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
            "robotsix_llmio.core.factory.get_provider_for_identifier",
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
