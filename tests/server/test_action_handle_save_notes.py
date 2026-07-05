"""Tests for ``_BoardActionMixin._handle_save_notes``.

Verifies that notes are persisted to the database with whitespace preserved
(no-strip behaviour), and that missing message_id returns a 400.
"""

from __future__ import annotations

from robotsix_auto_mail.db import get_record_by_message_id, init_db
from tests.server._test_helpers import _FakeHandler
from tests.server.conftest import _populate_db


class TestHandleSaveNotes:
    def test_notes_not_stripped(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "notes-test",
                    "sender": "x@x.com",
                    "subject": "Notes",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 80
        # URL-encoded: spaces become '+', %20, or actual spaces after
        # decoding.  parse_qs doesn't strip.  We include leading/trailing
        # spaces in the encoded form.
        handler.rfile.read.return_value = (
            b"message_id=notes-test&redirect_to=/board&notes=+++preserve+spaces+++"
        )

        handler._handle_save_notes()

        conn = init_db(single_db)
        try:
            record = get_record_by_message_id(conn, "notes-test")
            assert record is not None
            # The notes field should have leading/trailing spaces preserved.
            assert record.notes.startswith("   ")
            assert record.notes.endswith("   ")
            assert "preserve spaces" in record.notes
        finally:
            conn.close()

    def test_notes_persisted_to_db(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "notes-persist",
                    "sender": "x@x.com",
                    "subject": "Notes persist",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 90
        handler.rfile.read.return_value = (
            b"message_id=notes-persist&redirect_to=/board&notes=Hello+World"
        )

        handler._handle_save_notes()

        conn = init_db(single_db)
        try:
            record = get_record_by_message_id(conn, "notes-persist")
            assert record is not None
            assert record.notes == "Hello World"
        finally:
            conn.close()

    def test_missing_message_id_returns_400(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 40
        handler.rfile.read.return_value = b"notes=some+notes&redirect_to=/board"

        handler._handle_save_notes()
        handler._bad_request.assert_called_once_with("Missing message_id")
