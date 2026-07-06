"""Tests for propose_archive_subfolder_llm."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from robotsix_auto_mail.db import (
    MailRecord,
    init_db,
    insert_record,
    set_watermark,
)
from robotsix_auto_mail.triage import (
    ArchiveSubfolderProposal,
    _load_llm_archive_hints,
    get_archive_subfolder,
    propose_archive_subfolder_llm,
)
from tests.conftest import _make_record


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
# propose_archive_subfolder_llm
# ---------------------------------------------------------------------------


def _patch_llm_for_proposal(
    subfolder: str,
) -> tuple[mock.MagicMock, mock._patch[mock.MagicMock]]:
    """Patch get_provider to return *subfolder* from the LLM.

    Returns the mock handle (to assert ``close()``) and the patcher.
    """
    mock_run_result = mock.MagicMock()
    mock_run_result.output = ArchiveSubfolderProposal(subfolder=subfolder)
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
            "robotsix_llmio.core.factory.get_provider_for_identifier"
        ) as cls:
            propose_archive_subfolder_llm(conn, record, api_key="")

        # LLM never called
        cls.assert_not_called()

        # No hint stored
        hints = _load_llm_archive_hints(conn)
        assert "<nokey@x.com>" not in hints

        # Falls through to deterministic (root, domain/sender rule removed)
        result = get_archive_subfolder(conn, "<nokey@x.com>", record)
        assert result == ""
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
            "robotsix_llmio.core.factory.get_provider_for_identifier",
            return_value=mock_provider,
        ):
            propose_archive_subfolder_llm(conn, record, api_key="sk-test")

        # No hint stored
        hints = _load_llm_archive_hints(conn)
        assert "<error@x.com>" not in hints

        # Falls through to deterministic (root, domain/sender rule removed)
        result = get_archive_subfolder(conn, "<error@x.com>", record)
        assert result == ""
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
            "robotsix_llmio.core.factory.get_provider_for_identifier",
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


def test_propose_archive_subfolder_llm_rules_in_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-empty ``rules`` argument is injected into the system prompt."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<rules@x.com>", sender="alice@example.com")
        record = _make_record(
            message_id="<rules@x.com>",
            sender="alice@example.com",
            subject="Monthly report",
            date="2025-06-01T12:00:00",
        )

        rules_text = "- Mail from alice@example.com goes to Work/Reports."

        mock_handle = mock.MagicMock()
        mock_run_result = mock.MagicMock()
        mock_run_result.output = ArchiveSubfolderProposal(subfolder="Work/Reports")
        mock_handle.run_sync.return_value = mock_run_result

        mock_provider = mock.MagicMock()
        mock_provider.build_agent.return_value = mock_handle
        mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

        with mock.patch(
            "robotsix_llmio.core.factory.get_provider_for_identifier",
            return_value=mock_provider,
        ):
            propose_archive_subfolder_llm(
                conn, record, api_key="sk-test", rules=rules_text
            )

        # Verify system prompt includes the injected rules text.
        call_kwargs = mock_provider.build_agent.call_args[1]
        system_prompt = call_kwargs["system_prompt"]
        assert rules_text in system_prompt
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

        # Falls through to deterministic (root, domain/sender rule removed)
        result = get_archive_subfolder(conn, "<empty@x.com>", record)
        assert result == ""
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# get_archive_subfolder on-the-fly LLM proposal (new in PR #345 follow-up)
# ---------------------------------------------------------------------------


def test_hintless_with_api_key_uses_llm_and_persists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When an API key is provided and no hint exists, get_archive_subfolder
    calls the LLM, persists the hint, and returns it."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<api-key-test@x.com>", sender="dev@python.org")
        record = _make_record(
            message_id="<api-key-test@x.com>",
            sender="dev@python.org",
            subject="[python-dev] PEP discussion",
            date="2025-06-01T12:00:00",
            body_plain="Let's discuss the new PEP.",
        )

        set_watermark(
            conn,
            "archive_structure",
            json.dumps(["Lists/python-dev", "Receipts/2025"]),
        )

        handle, patcher = _patch_llm_for_proposal("Lists/python-dev")
        with patcher:
            result = get_archive_subfolder(
                conn, "<api-key-test@x.com>", record, api_key="sk-test"
            )

        # The LLM result is returned directly.
        assert result == "Lists/python-dev"

        # The hint is persisted so a second call is free.
        hints = _load_llm_archive_hints(conn)
        assert hints.get("<api-key-test@x.com>") == "Lists/python-dev"

        handle.close.assert_called_once()
    finally:
        conn.close()


def test_hintless_without_api_key_returns_root() -> None:
    """Ordinary sender, no API key → archive root ('')."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<no-key@x.com>", sender="alice@example.com")
        record = _make_record(
            message_id="<no-key@x.com>",
            sender="alice@example.com",
            subject="Hello",
            date="2025-06-01T12:00:00",
        )
        result = get_archive_subfolder(conn, "<no-key@x.com>", record)
        assert result == ""
    finally:
        conn.close()


def test_hintless_without_api_key_mailing_list() -> None:
    """Mailing-list subject, no API key → Lists/<name>."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<ml-no-key@x.com>", sender="a@b.com")
        record = _make_record(
            message_id="<ml-no-key@x.com>",
            sender="a@b.com",
            subject="[python-dev] Re: some topic",
            date="2025-06-01T12:00:00",
        )
        result = get_archive_subfolder(conn, "<ml-no-key@x.com>", record)
        assert result == "Lists/python-dev"
    finally:
        conn.close()
