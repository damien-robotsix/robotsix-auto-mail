"""Tests for delete_record_by_message_id."""

from __future__ import annotations

from robotsix_auto_mail.db import (
    delete_record_by_message_id,
    get_record_by_message_id,
    init_db,
    insert_record,
)
from tests.conftest import _make_record

# ---------------------------------------------------------------------------
# delete_record_by_message_id
# ---------------------------------------------------------------------------


def test_delete_record_by_message_id_success(tmp_db_path: str) -> None:
    """Deletes the mail_records row and its triage_decisions row."""
    from robotsix_auto_mail.triage import get_triage_decision, set_triage_decision

    conn = init_db(tmp_db_path)
    try:
        record = _make_record(message_id="<del-me@x.com>")
        insert_record(conn, record)
        set_triage_decision(conn, "<del-me@x.com>", "TO_DELETE", source="user")

        result = delete_record_by_message_id(conn, "<del-me@x.com>")
        assert result is True

        # Both rows are gone.
        assert get_record_by_message_id(conn, "<del-me@x.com>") is None
        assert get_triage_decision(conn, "<del-me@x.com>") is None
    finally:
        conn.close()


def test_delete_record_by_message_id_nonexistent(tmp_db_path: str) -> None:
    """Returns False when the message_id does not exist."""
    conn = init_db(tmp_db_path)
    try:
        result = delete_record_by_message_id(conn, "<no-such@x.com>")
        assert result is False
    finally:
        conn.close()


def test_delete_record_by_message_id_no_triage_decision(
    tmp_db_path: str,
) -> None:
    """Deletes the mail_records row even when no triage_decisions row exists."""
    conn = init_db(tmp_db_path)
    try:
        record = _make_record(message_id="<no-triage-decision@x.com>")
        insert_record(conn, record)

        result = delete_record_by_message_id(conn, "<no-triage-decision@x.com>")
        assert result is True
        assert get_record_by_message_id(conn, "<no-triage-decision@x.com>") is None
    finally:
        conn.close()
