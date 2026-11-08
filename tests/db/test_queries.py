"""Unit tests for previously-untested functions in ``db/queries.py``.

All tests run against a real in-memory SQLite connection — no mocks.
"""

from __future__ import annotations

import sqlite3

from robotsix_auto_mail.db import (
    MailRecord,
    component_settings_count,
    delete_watermark,
    get_account_health,
    get_all_component_settings,
    get_component_setting,
    get_record_by_message_id,
    get_watermark,
    init_db,
    insert_record,
    list_archive_audit_entries,
    list_records,
    load_json_watermark,
    save_json_watermark,
    set_component_settings,
    set_watermark,
    update_calendar_correlation_id,
    update_calendar_event_ref,
    write_account_health,
    write_archive_audit_entry,
)
from robotsix_auto_mail.db.queries import _rows_to_mailrecords, row_to_mailrecord


def _audit(
    conn: sqlite3.Connection, message_id: str, dest_folder: str = "Archive/A"
) -> int:
    rowid = write_archive_audit_entry(
        conn,
        message_id=message_id,
        subject=f"subj {message_id}",
        sender="alice@example.com",
        date="2026-01-01",
        source_column="TO_ARCHIVE",
        source_folder="INBOX",
        dest_folder=dest_folder,
        proposal_source="llm",
    )
    assert rowid is not None
    return rowid


def _full_record(message_id: str = "<full@example.com>") -> MailRecord:
    return MailRecord(
        message_id=message_id,
        sender="bob@example.com",
        subject="Full subject",
        date="2026-02-03T04:05:06Z",
        imap_uid=123,
        source_folder="Work",
        recipients_json='{"to": ["a@example.com"], "cc": []}',
        body_plain="plain body",
        body_html="<p>html</p>",
        attachments_json='[{"name": "a.pdf"}]',
        unsubscribe_header="<mailto:u@example.com>",
        notes="some notes",
        draft_text="draft",
        sent_reply_text="sent",
        calendar_event_ref="evt-1",
        calendar_correlation_id="corr-1",
    )


# ---------------------------------------------------------------------------
# Archive audit log
# ---------------------------------------------------------------------------


def test_write_archive_audit_entry_persists_all_fields() -> None:
    conn = init_db(":memory:")
    try:
        rowid = write_archive_audit_entry(
            conn,
            message_id="<m1@x>",
            subject="Hello",
            sender="alice@example.com",
            date="2026-01-01",
            source_column="TO_ARCHIVE",
            source_folder="INBOX",
            dest_folder="Archive/Bills",
            proposal_source="llm",
        )
        assert isinstance(rowid, int)
        entries = list_archive_audit_entries(conn)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["id"] == rowid
        assert entry["message_id"] == "<m1@x>"
        assert entry["subject"] == "Hello"
        assert entry["sender"] == "alice@example.com"
        assert entry["date"] == "2026-01-01"
        assert entry["source_column"] == "TO_ARCHIVE"
        assert entry["source_folder"] == "INBOX"
        assert entry["dest_folder"] == "Archive/Bills"
        assert entry["proposal_source"] == "llm"
        assert entry["archived_at"]
    finally:
        conn.close()


def test_list_archive_audit_entries_empty() -> None:
    conn = init_db(":memory:")
    try:
        assert list_archive_audit_entries(conn) == []
    finally:
        conn.close()


def test_list_archive_audit_entries_orders_id_desc() -> None:
    conn = init_db(":memory:")
    try:
        ids = [_audit(conn, f"<m{i}@x>") for i in range(3)]
        entries = list_archive_audit_entries(conn)
        assert [e["id"] for e in entries] == list(reversed(ids))
        assert [e["message_id"] for e in entries] == ["<m2@x>", "<m1@x>", "<m0@x>"]
    finally:
        conn.close()


def test_list_archive_audit_entries_respects_limit() -> None:
    conn = init_db(":memory:")
    try:
        for i in range(5):
            _audit(conn, f"<m{i}@x>")
        entries = list_archive_audit_entries(conn, limit=2)
        assert [e["message_id"] for e in entries] == ["<m4@x>", "<m3@x>"]
    finally:
        conn.close()


def test_list_archive_audit_entries_filters_by_folder() -> None:
    conn = init_db(":memory:")
    try:
        _audit(conn, "<a@x>", dest_folder="Archive/A")
        _audit(conn, "<b@x>", dest_folder="Archive/B")
        _audit(conn, "<c@x>", dest_folder="Archive/A/Sub")
        entries = list_archive_audit_entries(conn, folder="Archive/A")
        assert [e["message_id"] for e in entries] == ["<a@x>"]
        assert list_archive_audit_entries(conn, folder="Nope") == []
    finally:
        conn.close()


def test_list_archive_audit_entries_filters_by_since() -> None:
    conn = init_db(":memory:")
    try:
        _audit(conn, "<a@x>")
        _audit(conn, "<b@x>")
        assert len(list_archive_audit_entries(conn, since="2000-01-01")) == 2
        assert list_archive_audit_entries(conn, since="9999-12-31") == []
    finally:
        conn.close()


def test_list_archive_audit_entries_combines_since_and_folder() -> None:
    conn = init_db(":memory:")
    try:
        _audit(conn, "<a@x>", dest_folder="Archive/A")
        _audit(conn, "<b@x>", dest_folder="Archive/B")
        entries = list_archive_audit_entries(
            conn, since="2000-01-01", folder="Archive/B"
        )
        assert [e["message_id"] for e in entries] == ["<b@x>"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# JSON watermarks
# ---------------------------------------------------------------------------


def test_save_json_watermark_round_trips() -> None:
    conn = init_db(":memory:")
    try:
        data: dict[str, object] = {"a": 1, "b": ["x", "y"], "c": {"n": None}}
        save_json_watermark(conn, "k", data)
        assert load_json_watermark(conn, "k") == data
    finally:
        conn.close()


def test_save_json_watermark_overwrites() -> None:
    conn = init_db(":memory:")
    try:
        save_json_watermark(conn, "k", {"v": 1})
        save_json_watermark(conn, "k", {"v": 2})
        assert load_json_watermark(conn, "k") == {"v": 2}
    finally:
        conn.close()


def test_delete_watermark_removes_key() -> None:
    conn = init_db(":memory:")
    try:
        set_watermark(conn, "imap_uid", "42")
        set_watermark(conn, "other", "keep")
        delete_watermark(conn, "imap_uid")
        assert get_watermark(conn, "imap_uid") is None
        assert get_watermark(conn, "other") == "keep"
    finally:
        conn.close()


def test_delete_watermark_absent_key_is_noop() -> None:
    conn = init_db(":memory:")
    try:
        delete_watermark(conn, "missing")
        assert get_watermark(conn, "missing") is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Account health
# ---------------------------------------------------------------------------


def test_account_health_round_trip() -> None:
    conn = init_db(":memory:")
    try:
        write_account_health(
            conn, status="failed", error="auth", checked_at="2026-01-01T00:00:00Z"
        )
        assert get_account_health(conn) == {
            "status": "failed",
            "error": "auth",
            "checked_at": "2026-01-01T00:00:00Z",
        }
        write_account_health(
            conn, status="ok", error=None, checked_at="2026-01-02T00:00:00Z"
        )
        assert get_account_health(conn) == {
            "status": "ok",
            "error": None,
            "checked_at": "2026-01-02T00:00:00Z",
        }
    finally:
        conn.close()


def test_get_account_health_absent_returns_none() -> None:
    conn = init_db(":memory:")
    try:
        assert get_account_health(conn) is None
    finally:
        conn.close()


def test_get_account_health_corrupt_json_returns_none() -> None:
    conn = init_db(":memory:")
    try:
        set_watermark(conn, "account_health", "not json")
        assert get_account_health(conn) is None
    finally:
        conn.close()


def test_get_account_health_non_dict_json_returns_none() -> None:
    conn = init_db(":memory:")
    try:
        set_watermark(conn, "account_health", "[1,2]")
        assert get_account_health(conn) is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Calendar updates (exercise ``_update_column`` via public wrappers)
# ---------------------------------------------------------------------------


def test_update_calendar_event_ref_existing_record() -> None:
    conn = init_db(":memory:")
    try:
        insert_record(conn, MailRecord("<c@x>", "s@x", "subj", "2026-01-01"))
        assert update_calendar_event_ref(conn, "<c@x>", "evt-42") is True
        rec = get_record_by_message_id(conn, "<c@x>")
        assert rec is not None
        assert rec.calendar_event_ref == "evt-42"
        assert rec.calendar_correlation_id == ""
    finally:
        conn.close()


def test_update_calendar_event_ref_missing_record() -> None:
    conn = init_db(":memory:")
    try:
        assert update_calendar_event_ref(conn, "<missing@x>", "evt") is False
    finally:
        conn.close()


def test_update_calendar_correlation_id_existing_record() -> None:
    conn = init_db(":memory:")
    try:
        insert_record(conn, MailRecord("<c@x>", "s@x", "subj", "2026-01-01"))
        assert update_calendar_correlation_id(conn, "<c@x>", "corr-7") is True
        rec = get_record_by_message_id(conn, "<c@x>")
        assert rec is not None
        assert rec.calendar_correlation_id == "corr-7"
        assert rec.calendar_event_ref == ""
    finally:
        conn.close()


def test_update_calendar_correlation_id_missing_record() -> None:
    conn = init_db(":memory:")
    try:
        assert update_calendar_correlation_id(conn, "<missing@x>", "c") is False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Component settings
# ---------------------------------------------------------------------------


def test_component_settings_empty_state() -> None:
    conn = init_db(":memory:")
    try:
        assert get_component_setting(conn, "missing") is None
        assert get_all_component_settings(conn) == {}
        assert component_settings_count(conn) == 0
    finally:
        conn.close()


def test_component_settings_round_trip() -> None:
    conn = init_db(":memory:")
    try:
        set_component_settings(conn, {"zeta": "1", "alpha": "2", "mid": "3"})
        assert get_component_setting(conn, "alpha") == "2"
        assert get_component_setting(conn, "nope") is None
        all_settings = get_all_component_settings(conn)
        assert all_settings == {"alpha": "2", "mid": "3", "zeta": "1"}
        assert list(all_settings) == ["alpha", "mid", "zeta"]
    finally:
        conn.close()


def test_component_settings_count_tracks_upserts() -> None:
    conn = init_db(":memory:")
    try:
        set_component_settings(conn, {"a": "1", "b": "2"})
        assert component_settings_count(conn) == 2
        set_component_settings(conn, {"a": "changed"})
        assert component_settings_count(conn) == 2
        assert get_component_setting(conn, "a") == "changed"
        set_component_settings(conn, {"c": "3"})
        assert component_settings_count(conn) == 3
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Row mapping
# ---------------------------------------------------------------------------


def test_row_to_mailrecord_maps_full_row() -> None:
    conn = init_db(":memory:")
    try:
        original = _full_record()
        rowid = insert_record(conn, original)
        cur = conn.execute(
            "SELECT * FROM mail_records WHERE message_id = ?",
            (original.message_id,),
        )
        row = cur.fetchone()
        col_names = [desc[0] for desc in cur.description]
        rec = row_to_mailrecord(row, col_names)
        assert rec.id == rowid
        assert rec.message_id == original.message_id
        assert rec.sender == original.sender
        assert rec.subject == original.subject
        assert rec.date == original.date
        assert rec.status == original.status
        assert rec.imap_uid == 123
        assert rec.source_folder == "Work"
        assert rec.recipients_json == original.recipients_json
        assert rec.body_plain == "plain body"
        assert rec.body_html == "<p>html</p>"
        assert rec.attachments_json == original.attachments_json
        assert rec.unsubscribe_header == original.unsubscribe_header
        assert rec.notes == "some notes"
        assert rec.draft_text == "draft"
        assert rec.sent_reply_text == "sent"
        assert rec.calendar_event_ref == "evt-1"
        assert rec.calendar_correlation_id == "corr-1"
    finally:
        conn.close()


def test_row_to_mailrecord_nullable_and_empty_columns() -> None:
    conn = init_db(":memory:")
    try:
        insert_record(conn, MailRecord("<min@x>", "s@x", "", "2026-01-01"))
        cur = conn.execute("SELECT * FROM mail_records")
        row = cur.fetchone()
        col_names = [desc[0] for desc in cur.description]
        rec = row_to_mailrecord(row, col_names)
        assert rec.imap_uid is None
        assert rec.subject == ""
        assert rec.notes == ""
        assert rec.calendar_event_ref == ""
        assert rec.calendar_correlation_id == ""
    finally:
        conn.close()


def test_row_to_mailrecord_missing_calendar_columns_fallback() -> None:
    col_names = [
        "id",
        "imap_uid",
        "source_folder",
        "message_id",
        "sender",
        "subject",
        "date",
        "recipients_json",
        "body_plain",
        "body_html",
        "attachments_json",
        "unsubscribe_header",
        "status",
        "notes",
        "draft_text",
        "sent_reply_text",
    ]
    row = (
        7,
        5,
        "INBOX",
        "<legacy@x>",
        "s@x",
        "subj",
        "2026-01-01",
        '{"to": [], "cc": []}',
        "",
        "",
        "[]",
        "",
        MailRecord("<m>", "s", "s", "d").status,
        "",
        "",
        "",
    )
    rec = row_to_mailrecord(row, col_names)
    assert rec.id == 7
    assert rec.message_id == "<legacy@x>"
    assert rec.imap_uid == 5
    assert rec.calendar_event_ref == ""
    assert rec.calendar_correlation_id == ""


def test_rows_to_mailrecords_preserves_order() -> None:
    conn = init_db(":memory:")
    try:
        for i in range(3):
            insert_record(conn, MailRecord(f"<r{i}@x>", "s@x", f"s{i}", "d"))
        cur = conn.execute("SELECT * FROM mail_records ORDER BY id DESC")
        records = _rows_to_mailrecords(cur, cur.fetchall())
        assert [r.message_id for r in records] == ["<r2@x>", "<r1@x>", "<r0@x>"]
        assert [r.message_id for r in list_records(conn)] == [
            "<r0@x>",
            "<r1@x>",
            "<r2@x>",
        ]
        cur = conn.execute("SELECT * FROM mail_records WHERE 0")
        assert _rows_to_mailrecords(cur, cur.fetchall()) == []
    finally:
        conn.close()
