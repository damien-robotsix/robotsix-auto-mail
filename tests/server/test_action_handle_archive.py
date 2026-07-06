"""Tests for ``_BoardActionMixin._handle_archive``.

Verifies that ``_handle_archive`` delegates to ``_archive_and_delete`` and
redirects on success.
"""

from __future__ import annotations

from robotsix_auto_mail.db import get_record_by_message_id, init_db
from tests.server._test_helpers import _FakeHandler
from tests.server.conftest import _populate_db


class TestHandleArchive:
    def test_delegates_to_archive_and_delete_and_redirects(
        self, single_db: str
    ) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-wrap",
                    "sender": "x@x.com",
                    "subject": "Archive wrap",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db, mail_config=None)
        handler.headers.get.return_value = 70
        handler.rfile.read.return_value = b"message_id=arch-wrap&redirect_to=/board"

        handler._handle_archive()

        # Should redirect (success path) and delete the local record.
        handler._redirect.assert_called_once_with("/board", code=302)
        conn = init_db(single_db)
        try:
            assert get_record_by_message_id(conn, "arch-wrap") is None
        finally:
            conn.close()
