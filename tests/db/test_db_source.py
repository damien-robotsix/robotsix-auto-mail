"""Tests for update_record_source."""
from __future__ import annotations

from robotsix_auto_mail.db import (
    get_record_by_message_id,
    init_db,
    insert_record,
    update_record_source,
)
from tests.conftest import _make_record


# ---------------------------------------------------------------------------
# update_record_source
# ---------------------------------------------------------------------------


def test_update_record_source_updates_folder_and_uid() -> None:
    """update_record_source updates source_folder and imap_uid on a matching row."""
    conn = init_db(":memory:")
    try:
        record = _make_record(
            message_id="<update-source@x.com>",
            source_folder="INBOX",
            imap_uid=10,
        )
        insert_record(conn, record)

        result = update_record_source(
            conn,
            "<update-source@x.com>",
            source_folder="[Gmail]/Sent Mail",
            imap_uid=99,
        )
        assert result is True

        updated = get_record_by_message_id(conn, "<update-source@x.com>")
        assert updated is not None
        assert updated.source_folder == "[Gmail]/Sent Mail"
        assert updated.imap_uid == 99
    finally:
        conn.close()


def test_update_record_source_nonexistent_returns_false() -> None:
    """update_record_source returns False when no row matches message_id."""
    conn = init_db(":memory:")
    try:
        result = update_record_source(
            conn,
            "<not-found@x.com>",
            source_folder="INBOX",
            imap_uid=1,
        )
        assert result is False
    finally:
        conn.close()
