"""Tests for archive subfolder normalization, the unified agent, and overrides."""

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
    TriageItem,
    TriageResult,
    _build_triage_system_prompt,
    _load_archive_overrides,
    _load_llm_archive_hints,
    _save_llm_archive_hints,
    classify_archive_destination,
    get_archive_subfolder,
    run_triage_agent,
    set_archive_subfolder_override,
)
from robotsix_auto_mail.triage.classifier import normalize_archive_subfolder
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
# normalize_archive_subfolder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Billing", "Billing"),  # already relative → unchanged
        ("Administratif/Préfecture-44", "Administratif/Préfecture-44"),
        ("  Billing  ", "Billing"),  # trimmed
        ("/Billing/", "Billing"),  # stray slashes
        ("", ""),
        # echoed archive root prefix is stripped (not double-prefixed)
        ("robotsix-mail-archive/Billing", "Billing"),
        ("robotsix-mail-archive/LS2N/sub", "LS2N/sub"),
        ("robotsix-mail-archive", ""),  # root only → keep in root
        ("robosix-mail-archive/Billing", "Billing"),  # LLM typo of the root
        # triage-action tokens are never used as folder names
        ("TO_DELETE", ""),
        ("TO_ARCHIVE/Billing", "Billing"),
        ("Billing/TO_DELETE", "Billing"),
    ],
)
def test_normalize_archive_subfolder(raw: str, expected: str) -> None:
    assert normalize_archive_subfolder(raw) == expected


# ---------------------------------------------------------------------------
# Unified agent: one LLM call assigns column + archive destination
# ---------------------------------------------------------------------------


def _patch_provider(result: TriageResult) -> tuple[mock.MagicMock, mock.MagicMock]:
    """Return ``(provider, handle)`` mocks whose agent yields *result*."""
    handle = mock.MagicMock()
    handle.run_sync.return_value = mock.MagicMock(output=result)
    provider = mock.MagicMock()
    provider.build_agent.return_value = handle
    provider.call_with_retry.side_effect = lambda fn, what: fn()
    return provider, handle


def test_run_triage_agent_single_llm_call_for_column_and_destination() -> None:
    """A TO_ARCHIVE decision without a subfolder triggers NO second LLM call."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x>")
        _insert_inbox(conn, "<b@x>")
        result = TriageResult(
            items=[
                TriageItem(index=1, action="TO_ARCHIVE", archive_subfolder="Finance"),
                TriageItem(index=2, action="TO_ARCHIVE"),
            ]
        )
        provider, handle = _patch_provider(result)
        with mock.patch(
            "robotsix_llmio.core.factory.get_provider_for_identifier",
            return_value=provider,
        ):
            run_triage_agent(conn, api_key="sk-test")
        assert handle.run_sync.call_count == 1
        hints = _load_llm_archive_hints(conn)
        assert hints == {"<a@x>": "Finance"}
        system_prompt = provider.build_agent.call_args[1]["system_prompt"]
        assert "archive_subfolder" in system_prompt
        assert "TO_ARCHIVE" in system_prompt
    finally:
        conn.close()


def test_classify_archive_destination_uses_unified_agent() -> None:
    """The single-message path reuses the triage prompt + TriageResult schema."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x>")
        rec = _make_record(message_id="<a@x>")
        result = TriageResult(
            items=[TriageItem(index=1, action="TO_ARCHIVE", archive_subfolder="Orders")]
        )
        provider, handle = _patch_provider(result)
        with mock.patch(
            "robotsix_llmio.core.factory.get_provider_for_identifier",
            return_value=provider,
        ):
            got = classify_archive_destination(conn, rec, api_key="sk-test")
        assert got == "Orders"
        assert _load_llm_archive_hints(conn) == {"<a@x>": "Orders"}
        assert handle.run_sync.call_count == 1
        kwargs = provider.build_agent.call_args[1]
        assert kwargs["system_prompt"] == _build_triage_system_prompt(None, None)
        assert "TO_ARCHIVE" in handle.run_sync.call_args[0][0]
    finally:
        conn.close()


def test_classify_archive_destination_no_api_key_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no resolvable API key the agent is never built and "" is returned."""
    monkeypatch.setattr(
        "robotsix_auto_mail.triage.agent.resolve_llm_api_key",
        lambda *a, **kw: "",
    )
    conn = init_db(":memory:")
    try:
        rec = _make_record(message_id="<a@x>")
        with mock.patch(
            "robotsix_llmio.core.factory.get_provider_for_identifier"
        ) as factory:
            assert classify_archive_destination(conn, rec, api_key="") == ""
        factory.assert_not_called()
        assert _load_llm_archive_hints(conn) == {}
    finally:
        conn.close()


def test_classify_archive_destination_llm_failure_returns_empty() -> None:
    """An LLM failure is swallowed — callers fall back to the archive root."""
    conn = init_db(":memory:")
    try:
        rec = _make_record(message_id="<a@x>")
        provider, handle = _patch_provider(TriageResult())
        handle.run_sync.side_effect = RuntimeError("boom")
        with mock.patch(
            "robotsix_llmio.core.factory.get_provider_for_identifier",
            return_value=provider,
        ):
            assert classify_archive_destination(conn, rec, api_key="sk-test") == ""
        assert _load_llm_archive_hints(conn) == {}
    finally:
        conn.close()


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
        # Default → deterministic (root, since no mailing-list prefix)
        default = get_archive_subfolder(conn, "<a@x.com>", record)
        assert default == ""

        # Set override
        set_archive_subfolder_override(conn, "<a@x.com>", "Custom/Folder")
        assert get_archive_subfolder(conn, "<a@x.com>", record) == "Custom/Folder"

        # Clear override (empty string)
        set_archive_subfolder_override(conn, "<a@x.com>", "")
        assert get_archive_subfolder(conn, "<a@x.com>", record) == ""
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
