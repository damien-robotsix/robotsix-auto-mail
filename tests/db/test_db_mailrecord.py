"""Tests for MailRecord construction, defaults, and notes."""

from __future__ import annotations

import dataclasses

import pytest

from robotsix_auto_mail.db import (
    MailRecord,
    get_record_by_message_id,
    init_db,
    insert_record,
)
from tests.conftest import _make_record

# ---------------------------------------------------------------------------
# MailRecord construction and defaults
# ---------------------------------------------------------------------------


def test_mailrecord_required_fields() -> None:
    """Minimum required fields produce a valid MailRecord."""
    record = MailRecord(
        message_id="<abc@example.com>",
        sender="alice@example.com",
        subject="Hello",
        date="2025-01-15T10:30:00Z",
    )
    assert record.message_id == "<abc@example.com>"
    assert record.sender == "alice@example.com"
    assert record.subject == "Hello"
    assert record.date == "2025-01-15T10:30:00Z"
    # Defaults
    assert record.imap_uid is None
    assert record.recipients_json == '{"to": [], "cc": []}'
    assert record.body_plain == ""
    assert record.body_html == ""
    assert record.attachments_json == "[]"
    assert record.status == "to_read"
    assert record.id == 0


def test_mailrecord_is_frozen() -> None:
    """MailRecord is immutable."""
    record = MailRecord(
        message_id="<abc@example.com>",
        sender="a@x.com",
        subject="S",
        date="2025-01-01",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.subject = "Changed"  # type: ignore[misc]


def test_mailrecord_all_fields_explicit() -> None:
    """All fields can be set explicitly."""
    record = MailRecord(
        message_id="<def@example.com>",
        sender="bob@example.com",
        subject="Test",
        date="2025-06-01",
        imap_uid=42,
        recipients_json='{"to": ["x@x.com"], "cc": ["y@y.com"]}',
        body_plain="Hello world",
        body_html="<p>Hello world</p>",
        attachments_json='[{"filename": "a.pdf", "size": 1024}]',
        id=0,
    )
    assert record.status == "to_read"
    assert record.imap_uid == 42
    assert record.recipients_json == '{"to": ["x@x.com"], "cc": ["y@y.com"]}'
    assert record.body_plain == "Hello world"
    assert record.body_html == "<p>Hello world</p>"
    assert record.attachments_json == '[{"filename": "a.pdf", "size": 1024}]'


def test_mailrecord_status_explicit() -> None:
    """status can be set explicitly to a non-default value."""
    record = MailRecord(
        message_id="<status@example.com>",
        sender="x@x.com",
        subject="S",
        date="2025-01-01",
        status="needs_reply",
    )
    assert record.status == "needs_reply"


def test_mailrecord_notes_default() -> None:
    """notes defaults to '' when not provided."""
    record = MailRecord(
        message_id="<notes-default@example.com>",
        sender="x@x.com",
        subject="S",
        date="2025-01-01",
    )
    assert record.notes == ""


def test_mailrecord_notes_explicit() -> None:
    """notes can be set explicitly."""
    record = MailRecord(
        message_id="<notes-explicit@example.com>",
        sender="x@x.com",
        subject="S",
        date="2025-01-01",
        notes="Follow up with Alice",
    )
    assert record.notes == "Follow up with Alice"


def test_insert_record_round_trips_notes() -> None:
    """insert_record + get_record_by_message_id round-trips the notes field."""
    conn = init_db(":memory:")
    try:
        record = MailRecord(
            message_id="<notes-rt@example.com>",
            sender="x@x.com",
            subject="S",
            date="2025-01-01",
            notes="Wait for reply",
        )
        insert_record(conn, record)
        result = get_record_by_message_id(conn, "<notes-rt@example.com>")
        assert result is not None
        assert result.notes == "Wait for reply"
    finally:
        conn.close()


def test_update_notes_sets_and_persists() -> None:
    """update_notes sets notes on an existing record, verifiable via get_record."""
    from robotsix_auto_mail.db import update_notes

    conn = init_db(":memory:")
    try:
        record = _make_record(message_id="<update-me@x.com>")
        insert_record(conn, record)

        result = update_notes(conn, "<update-me@x.com>", "new note text")
        assert result is True

        record2 = get_record_by_message_id(conn, "<update-me@x.com>")
        assert record2 is not None
        assert record2.notes == "new note text"
    finally:
        conn.close()


def test_update_notes_nonexistent_returns_false() -> None:
    """update_notes returns False for a nonexistent message_id."""
    from robotsix_auto_mail.db import update_notes

    conn = init_db(":memory:")
    try:
        result = update_notes(conn, "<nonexistent@x.com>", "whatever")
        assert result is False
    finally:
        conn.close()
