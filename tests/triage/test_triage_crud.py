"""Tests for triage CRUD operations (set/get/delete triage decisions)."""

from __future__ import annotations

import os
import tempfile

import pytest

from robotsix_auto_mail.db import (
    MailRecord,
    get_record_by_message_id,
    init_db,
    insert_record,
    list_untriaged_records,
    update_sent_reply_text,
)
from robotsix_auto_mail.triage import (
    TriageError,
    delete_triage_decision,
    delete_triage_decisions_by_action,
    get_triage_decision,
    list_triage_decisions,
    set_triage_decision,
)
from robotsix_auto_mail.triage.agent import _build_user_message


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
# set_triage_decision validation
# ---------------------------------------------------------------------------


def test_set_triage_decision_rejects_invalid_action() -> None:
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        with pytest.raises(TriageError):
            set_triage_decision(conn, "<a@x.com>", "banana", source="user")
    finally:
        conn.close()


def test_set_triage_decision_rejects_invalid_source() -> None:
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        with pytest.raises(TriageError):
            set_triage_decision(conn, "<a@x.com>", "TO_ANSWER", source="robot")
    finally:
        conn.close()


def test_set_triage_decision_upserts() -> None:
    """A second call for the same message_id overwrites the first."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        set_triage_decision(conn, "<a@x.com>", "TO_ANSWER", source="agent")
        set_triage_decision(
            conn, "<a@x.com>", "TO_ARCHIVE", source="user", reason="mine"
        )
        decision = get_triage_decision(conn, "<a@x.com>")
        assert decision is not None
        assert decision.action == "TO_ARCHIVE"
        assert decision.source == "user"
        assert decision.reason == "mine"
        # Still exactly one row.
        assert len(list_triage_decisions(conn)) == 1
    finally:
        conn.close()


def test_get_triage_decision_missing_returns_none() -> None:
    conn = init_db(":memory:")
    try:
        assert get_triage_decision(conn, "<nope@x.com>") is None
    finally:
        conn.close()


def test_list_triage_decisions_filters_by_source() -> None:
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        _insert_inbox(conn, "<b@x.com>")
        set_triage_decision(conn, "<a@x.com>", "TO_ANSWER", source="agent")
        set_triage_decision(conn, "<b@x.com>", "TO_ARCHIVE", source="user")
        agent_only = list_triage_decisions(conn, source="agent")
        assert [d.message_id for d in agent_only] == ["<a@x.com>"]
        user_only = list_triage_decisions(conn, source="user")
        assert [d.message_id for d in user_only] == ["<b@x.com>"]
        assert len(list_triage_decisions(conn)) == 2
    finally:
        conn.close()


def test_triage_decision_persists_across_connections() -> None:
    """A decision written on one connection is visible on another."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn1 = init_db(path)
        _insert_inbox(conn1, "<persisted@x.com>")
        set_triage_decision(conn1, "<persisted@x.com>", "TO_ANSWER", source="user")
        conn1.close()

        conn2 = init_db(path)
        decision = get_triage_decision(conn2, "<persisted@x.com>")
        assert decision is not None
        assert decision.action == "TO_ANSWER"
        assert decision.source == "user"
        conn2.close()
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# delete_triage_decisions_by_action
# ---------------------------------------------------------------------------


def test_delete_triage_decisions_by_action_happy_path() -> None:
    """Happy path: deletes all decisions for one action, returns count."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        _insert_inbox(conn, "<b@x.com>")
        _insert_inbox(conn, "<c@x.com>")
        set_triage_decision(conn, "<a@x.com>", "HUMAN_TRIAGE", source="user")
        set_triage_decision(conn, "<b@x.com>", "HUMAN_TRIAGE", source="user")
        set_triage_decision(conn, "<c@x.com>", "TO_ARCHIVE", source="user")

        deleted = delete_triage_decisions_by_action(conn, "HUMAN_TRIAGE")
        assert deleted == 2
        remaining = list_triage_decisions(conn)
        assert len(remaining) == 1
        assert remaining[0].action == "TO_ARCHIVE"
    finally:
        conn.close()


def test_delete_triage_decisions_by_action_rejects_inbox() -> None:
    """action='INBOX' raises TriageError."""
    conn = init_db(":memory:")
    try:
        with pytest.raises(TriageError, match="INBOX"):
            delete_triage_decisions_by_action(conn, "INBOX")
    finally:
        conn.close()


def test_delete_triage_decisions_by_action_invalid_action() -> None:
    """Invalid action raises TriageError."""
    conn = init_db(":memory:")
    try:
        with pytest.raises(TriageError, match="Invalid triage action"):
            delete_triage_decisions_by_action(conn, "BOGUS")
    finally:
        conn.close()


def test_delete_triage_decisions_by_action_no_matching_rows() -> None:
    """Zero matching rows returns 0, no error."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        set_triage_decision(conn, "<a@x.com>", "TO_ARCHIVE", source="user")
        deleted = delete_triage_decisions_by_action(conn, "HUMAN_TRIAGE")
        assert deleted == 0
        assert len(list_triage_decisions(conn)) == 1
    finally:
        conn.close()


def test_delete_triage_decision_requeues_record() -> None:
    """Deleting a record's decision makes it untriaged again."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>")
        set_triage_decision(conn, "<a@x.com>", "TO_ANSWER", source="user")
        assert get_triage_decision(conn, "<a@x.com>") is not None
        assert "<a@x.com>" not in {r.message_id for r in list_untriaged_records(conn)}

        assert delete_triage_decision(conn, "<a@x.com>") is True
        assert get_triage_decision(conn, "<a@x.com>") is None
        assert "<a@x.com>" in {r.message_id for r in list_untriaged_records(conn)}

        # Deleting again is a no-op (no row to remove).
        assert delete_triage_decision(conn, "<a@x.com>") is False
    finally:
        conn.close()


def test_update_sent_reply_text_and_column_default() -> None:
    """sent_reply_text defaults to '' and update_sent_reply_text persists it."""
    conn = init_db(":memory:")
    try:
        # Column present on mail_records.
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(mail_records)").fetchall()
        }
        assert "sent_reply_text" in cols

        _insert_inbox(conn, "<a@x.com>")
        record = get_record_by_message_id(conn, "<a@x.com>")
        assert record is not None
        assert record.sent_reply_text == ""

        assert update_sent_reply_text(conn, "<a@x.com>", "My reply body.") is True
        updated = get_record_by_message_id(conn, "<a@x.com>")
        assert updated is not None
        assert updated.sent_reply_text == "My reply body."

        # No matching row → False.
        assert update_sent_reply_text(conn, "<missing>", "x") is False
    finally:
        conn.close()


def test_answered_record_untriaged_and_marked_in_user_message() -> None:
    """A record with sent_reply_text (and no decision) is untriaged and its
    user-message line carries the answered marker + reply preview."""
    conn = init_db(":memory:")
    try:
        _insert_inbox(conn, "<a@x.com>", subject="Question")
        update_sent_reply_text(conn, "<a@x.com>", "Thanks, all sorted.")

        untriaged = list_untriaged_records(conn)
        assert "<a@x.com>" in {r.message_id for r in untriaged}

        message = _build_user_message(untriaged)
        assert "ANSWERED — reply sent:" in message
        assert "Thanks, all sorted." in message
    finally:
        conn.close()
