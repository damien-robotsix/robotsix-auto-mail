"""Tests for insert, list, get, record_exists, and list_untriaged_records."""
from __future__ import annotations

import sqlite3

from robotsix_auto_mail.db import (
    MailRecord,
    get_record_by_message_id,
    init_db,
    insert_record,
    list_records,
    list_untriaged_records,
)
from tests.conftest import _make_record


# ---------------------------------------------------------------------------
# insert_record
# ---------------------------------------------------------------------------


def test_insert_record_returns_rowid() -> None:
    """Successful insert returns the new rowid."""
    conn = init_db(":memory:")
    try:
        record = _make_record()
        rowid = insert_record(conn, record)
        assert rowid is not None
        assert isinstance(rowid, int)
        assert rowid > 0
    finally:
        conn.close()


def test_insert_record_persists_data() -> None:
    """Inserted data can be read back from the table."""
    conn = init_db(":memory:")
    try:
        record = MailRecord(
            message_id="<m1@example.com>",
            sender="alice@example.com",
            subject="Hello",
            date="2025-06-01",
            imap_uid=10,
            recipients_json='{"to": ["bob@x.com"], "cc": []}',
            body_plain="Hi Bob",
            body_html="<p>Hi Bob</p>",
            attachments_json='[{"name": "file.txt"}]',
        )
        rowid = insert_record(conn, record)
        cur = conn.execute("SELECT * FROM mail_records WHERE id = ?", (rowid,))
        row = cur.fetchone()
        assert row is not None
        col_names = [desc[0] for desc in cur.description]
        data = dict(zip(col_names, row, strict=True))
        assert data["message_id"] == "<m1@example.com>"
        assert data["sender"] == "alice@example.com"
        assert data["subject"] == "Hello"
        assert data["date"] == "2025-06-01"
        assert data["imap_uid"] == 10
        assert data["recipients_json"] == '{"to": ["bob@x.com"], "cc": []}'
        assert data["body_plain"] == "Hi Bob"
        assert data["body_html"] == "<p>Hi Bob</p>"
        assert data["attachments_json"] == '[{"name": "file.txt"}]'
        assert data["status"] == "to_read"
    finally:
        conn.close()


def test_insert_record_ignores_id_field() -> None:
    """The id field of the input record is ignored; DB assigns it."""
    conn = init_db(":memory:")
    try:
        record = _make_record(message_id="<id-test@example.com>", id=9999)
        rowid = insert_record(conn, record)
        assert rowid is not None
        # The DB-assigned id should be the rowid, not 9999.
        cur = conn.execute(
            "SELECT id FROM mail_records WHERE message_id = ?",
            ("<id-test@example.com>",),
        )
        db_id = cur.fetchone()[0]
        assert db_id == rowid
    finally:
        conn.close()


def test_insert_record_unique_constraint_returns_none() -> None:
    """Inserting the same message_id twice returns None on the second call."""
    conn = init_db(":memory:")
    try:
        record = _make_record(message_id="<dup@example.com>")
        first = insert_record(conn, record)
        assert first is not None
        second = insert_record(conn, record)
        assert second is None
    finally:
        conn.close()


def test_insert_record_unique_constraint_no_exception() -> None:
    """Duplicate message_id does NOT raise — it returns None."""
    conn = init_db(":memory:")
    try:
        record = _make_record(message_id="<no-raise@example.com>")
        insert_record(conn, record)
        # Should not raise
        result = insert_record(conn, record)
        assert result is None
    finally:
        conn.close()


def test_insert_record_unique_constraint_only_one_row() -> None:
    """Duplicate insert leaves exactly one row in the table."""
    conn = init_db(":memory:")
    try:
        record = _make_record(message_id="<one-row@example.com>")
        insert_record(conn, record)
        insert_record(conn, record)  # duplicate
        cur = conn.execute(
            "SELECT COUNT(*) FROM mail_records WHERE message_id = ?",
            ("<one-row@example.com>",),
        )
        count = cur.fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_insert_record_imap_uid_nullable() -> None:
    """imap_uid can be None (NULL in DB)."""
    conn = init_db(":memory:")
    try:
        record = _make_record(message_id="<no-uid@example.com>", imap_uid=None)
        rowid = insert_record(conn, record)
        cur = conn.execute("SELECT imap_uid FROM mail_records WHERE id = ?", (rowid,))
        val = cur.fetchone()[0]
        assert val is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# list_records
# ---------------------------------------------------------------------------


def test_list_records_empty_table() -> None:
    """list_records returns an empty list when mail_records is empty."""
    conn = init_db(":memory:")
    try:
        result = list_records(conn)
        assert isinstance(result, list)
        assert len(result) == 0
    finally:
        conn.close()


def test_list_records_returns_all_fields() -> None:
    """Every field of an inserted MailRecord round-trips through list_records."""
    conn = init_db(":memory:")
    try:
        attachments_json_val = (
            '[{"filename": "f1.pdf", "size": 2048}, '
            '{"filename": "f2.txt", "size": 512}]'
        )
        record = MailRecord(
            message_id="<all-fields@example.com>",
            sender="sender@example.com",
            subject="All Fields Test",
            date="2025-07-01T10:00:00Z",
            imap_uid=77,
            recipients_json='{"to": ["a@b.com"], "cc": ["c@d.com"]}',
            body_plain="Plain text body.",
            body_html="<p>HTML body.</p>",
            attachments_json=attachments_json_val,
            notes="test notes",
        )
        insert_record(conn, record)
        results = list_records(conn)
        assert len(results) == 1
        r = results[0]
        assert r.message_id == "<all-fields@example.com>"
        assert r.sender == "sender@example.com"
        assert r.subject == "All Fields Test"
        assert r.date == "2025-07-01T10:00:00Z"
        assert r.imap_uid == 77
        assert r.recipients_json == '{"to": ["a@b.com"], "cc": ["c@d.com"]}'
        assert r.body_plain == "Plain text body."
        assert r.body_html == "<p>HTML body.</p>"
        assert r.attachments_json == (
            '[{"filename": "f1.pdf", "size": 2048}, '
            '{"filename": "f2.txt", "size": 512}]'
        )
        assert r.status == "to_read"
        assert r.notes == "test notes"
        assert r.id is not None
        assert r.id > 0
    finally:
        conn.close()


def test_list_records_ordering() -> None:
    """list_records returns results ordered by id ASC regardless of insert order."""
    conn = init_db(":memory:")
    try:
        # Insert 3 records with message_ids that would sort differently
        # alphabetically: <c>, <a>, <b> -> auto-increment ids 1, 2, 3.
        for mid in ("<c@x.com>", "<a@x.com>", "<b@x.com>"):
            record = _make_record(message_id=mid)
            insert_record(conn, record)

        results = list_records(conn)
        assert len(results) == 3
        # Must be ordered by id ASC (insertion order = alphabetical order of
        # the message_ids in this case: c, a, b -> ids 1, 2, 3).
        assert results[0].message_id == "<c@x.com>"
        assert results[1].message_id == "<a@x.com>"
        assert results[2].message_id == "<b@x.com>"
    finally:
        conn.close()


def test_list_records_multiple_rows() -> None:
    """list_records returns the correct count and content for 3 records."""
    conn = init_db(":memory:")
    try:
        insert_record(
            conn,
            _make_record(
                message_id="<m1@x.com>",
                sender="alice@x.com",
                subject="First",
            ),
        )
        insert_record(
            conn,
            _make_record(
                message_id="<m2@x.com>",
                sender="bob@x.com",
                subject="Second",
            ),
        )
        insert_record(
            conn,
            _make_record(
                message_id="<m3@x.com>",
                sender="carol@x.com",
                subject="Third",
            ),
        )

        results = list_records(conn)
        assert len(results) == 3
        senders = [r.sender for r in results]
        subjects = [r.subject for r in results]
        assert senders == ["alice@x.com", "bob@x.com", "carol@x.com"]
        assert subjects == ["First", "Second", "Third"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# get_record_by_message_id
# ---------------------------------------------------------------------------


def test_get_record_by_message_id_found() -> None:
    """get_record_by_message_id returns a full MailRecord for a known id."""
    conn = init_db(":memory:")
    try:
        record = MailRecord(
            message_id="<lookup@example.com>",
            sender="lookup@example.com",
            subject="Lookup Test",
            date="2025-08-01T10:00:00Z",
            imap_uid=42,
            recipients_json='{"to": ["a@b.com"], "cc": ["c@d.com"]}',
            body_plain="Plain text here.",
            body_html="<p>HTML here.</p>",
            attachments_json='[{"filename": "doc.pdf", "size": 512}]',
            status="needs_reply",
        )
        insert_record(conn, record)

        result = get_record_by_message_id(conn, "<lookup@example.com>")
        assert result is not None
        assert result.message_id == "<lookup@example.com>"
        assert result.sender == "lookup@example.com"
        assert result.subject == "Lookup Test"
        assert result.date == "2025-08-01T10:00:00Z"
        assert result.imap_uid == 42
        assert result.recipients_json == '{"to": ["a@b.com"], "cc": ["c@d.com"]}'
        assert result.body_plain == "Plain text here."
        assert result.body_html == "<p>HTML here.</p>"
        assert result.attachments_json == '[{"filename": "doc.pdf", "size": 512}]'
        assert result.status == "needs_reply"
        assert result.id > 0
    finally:
        conn.close()


def test_get_record_by_message_id_not_found() -> None:
    """get_record_by_message_id returns None for an unknown message_id."""
    conn = init_db(":memory:")
    try:
        result = get_record_by_message_id(conn, "<nonexistent@x.com>")
        assert result is None
    finally:
        conn.close()


def test_get_record_by_message_id_angle_brackets() -> None:
    """message_id with angle brackets round-trips correctly."""
    conn = init_db(":memory:")
    try:
        mid = "<abc@example.com>"
        record = MailRecord(
            message_id=mid,
            sender="test@t.com",
            subject="Angle Brackets",
            date="2025-09-01T10:00:00Z",
        )
        insert_record(conn, record)

        result = get_record_by_message_id(conn, mid)
        assert result is not None
        assert result.message_id == mid
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# record_exists
# ---------------------------------------------------------------------------


def test_record_exists_returns_false_when_empty() -> None:
    """record_exists returns False when no matching row exists."""
    conn = init_db(":memory:")
    try:
        from robotsix_auto_mail.db import record_exists

        assert record_exists(conn, "<nonexistent@x>") is False
    finally:
        conn.close()


def test_record_exists_returns_true_after_insert() -> None:
    """record_exists returns True after a record is inserted."""
    conn = init_db(":memory:")
    try:
        from robotsix_auto_mail.db import record_exists

        record = _make_record(message_id="<exists@x>")
        insert_record(conn, record)
        assert record_exists(conn, "<exists@x>") is True
    finally:
        conn.close()


def test_record_exists_fresh_connection_no_error() -> None:
    """record_exists on a non-init_db connection (table created manually)
    returns False, not an error."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """\
CREATE TABLE IF NOT EXISTS mail_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    imap_uid        INTEGER,
    message_id      TEXT    NOT NULL UNIQUE,
    sender          TEXT    NOT NULL,
    subject         TEXT    NOT NULL,
    date            TEXT    NOT NULL,
    recipients_json TEXT    NOT NULL,
    body_plain      TEXT    NOT NULL,
    body_html       TEXT    NOT NULL,
    attachments_json TEXT   NOT NULL
)
"""
    )
    try:
        from robotsix_auto_mail.db import record_exists

        assert record_exists(conn, "<anything@x>") is False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# list_untriaged_records
# ---------------------------------------------------------------------------


def test_list_untriaged_records_empty() -> None:
    """list_untriaged_records returns [] when the table is empty."""
    conn = init_db(":memory:")
    try:
        assert list_untriaged_records(conn) == []
    finally:
        conn.close()


def test_list_untriaged_records_all_untriaged() -> None:
    """When no triage_decisions rows exist, every record is returned."""
    conn = init_db(":memory:")
    try:
        for mid in ("<a@x.com>", "<b@x.com>", "<c@x.com>"):
            insert_record(conn, _make_record(message_id=mid))
        result = list_untriaged_records(conn)
        assert len(result) == 3
        assert [r.message_id for r in result] == ["<a@x.com>", "<b@x.com>", "<c@x.com>"]
    finally:
        conn.close()


def test_list_untriaged_records_some_triaged() -> None:
    """Records with a triage_decisions row are excluded."""
    conn = init_db(":memory:")
    try:
        insert_record(conn, _make_record(message_id="<triaged@x.com>"))
        insert_record(conn, _make_record(message_id="<untriaged@x.com>"))
        conn.execute(
            "INSERT INTO triage_decisions "
            "(message_id, action, source, reason, confidence, updated_at) "
            "VALUES ('<triaged@x.com>', 'TO_ARCHIVE', 'agent', '', 'medium', '2025-01-01T00:00:00')"
        )
        conn.commit()
        result = list_untriaged_records(conn)
        assert len(result) == 1
        assert result[0].message_id == "<untriaged@x.com>"
    finally:
        conn.close()


def test_list_untriaged_records_ordered_by_id() -> None:
    """Results are ordered by id ASC."""
    conn = init_db(":memory:")
    try:
        # Insert in reverse alphabetical order to prove ordering is by id.
        for mid in ("<c@x.com>", "<a@x.com>", "<b@x.com>"):
            insert_record(conn, _make_record(message_id=mid))
        result = list_untriaged_records(conn)
        assert [r.message_id for r in result] == ["<c@x.com>", "<a@x.com>", "<b@x.com>"]
    finally:
        conn.close()
