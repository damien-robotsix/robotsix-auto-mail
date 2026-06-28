"""Tests for archive subfolder normalization, fill_missing, and get/set overrides."""

from __future__ import annotations

import os
import tempfile
from unittest import mock

import pytest
from tests.conftest import _make_record

from robotsix_auto_mail.db import (
    MailRecord,
    init_db,
    insert_record,
)
from robotsix_auto_mail.triage import (
    TriageItem,
    _load_archive_overrides,
    _save_llm_archive_hints,
    get_archive_subfolder,
    set_archive_subfolder_override,
)
from robotsix_auto_mail.triage.agent import _fill_missing_archive_hints
from robotsix_auto_mail.triage.classifier import normalize_archive_subfolder


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
# _fill_missing_archive_hints
# ---------------------------------------------------------------------------


def test_fill_missing_archive_hints_only_unhinted_to_archive() -> None:
    """The proposer runs only for TO_ARCHIVE records without an existing hint."""
    conn = init_db(":memory:")
    try:
        rec_a = _make_record(message_id="<a@x>")  # TO_ARCHIVE, no hint → propose
        rec_b = _make_record(message_id="<b@x>")  # TO_DELETE → skip
        rec_c = _make_record(message_id="<c@x>")  # TO_ARCHIVE, already hinted → skip
        _save_llm_archive_hints(conn, {"<c@x>": "Billing"})
        by_index = {
            1: TriageItem(index=1, action="TO_ARCHIVE"),
            2: TriageItem(index=2, action="TO_DELETE"),
            3: TriageItem(index=3, action="TO_ARCHIVE"),
        }
        with mock.patch(
            "robotsix_auto_mail.triage.agent.propose_archive_subfolder_llm"
        ) as proposer:
            _fill_missing_archive_hints(
                conn, [rec_a, rec_b, rec_c], by_index, "sk-test", None
            )
        called_ids = [call.args[1].message_id for call in proposer.call_args_list]
        assert called_ids == ["<a@x>"]
    finally:
        conn.close()


def test_fill_missing_archive_hints_no_api_key_is_noop() -> None:
    """With no API key the proposer is never invoked."""
    conn = init_db(":memory:")
    try:
        rec = _make_record(message_id="<a@x>")
        by_index = {1: TriageItem(index=1, action="TO_ARCHIVE")}
        with mock.patch(
            "robotsix_auto_mail.triage.agent.propose_archive_subfolder_llm"
        ) as proposer:
            _fill_missing_archive_hints(conn, [rec], by_index, "", None)
        proposer.assert_not_called()
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
