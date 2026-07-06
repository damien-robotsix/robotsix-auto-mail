"""Unit tests for ``_handle_delete``.

Covers local-only deletion (no IMAP config, no IMAP UID), IMAP server-
side deletion, cross-folder healing when the stored UID is stale, and
IMAP error handling (502 response, record preserved).
"""

from __future__ import annotations

from unittest import mock

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import get_record_by_message_id, init_db
from robotsix_auto_mail.imap import ImapError
from tests.server._test_helpers import _FakeHandler
from tests.server.conftest import _populate_db


class TestHandleDelete:
    def test_no_imap_config_deletes_locally(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "no-imap-del",
                    "sender": "x@x.com",
                    "subject": "No IMAP",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db, mail_config=None)
        handler.headers.get.return_value = 80
        handler.rfile.read.return_value = b"message_id=no-imap-del&redirect_to=/board"

        handler._handle_delete()

        conn = init_db(single_db)
        try:
            assert get_record_by_message_id(conn, "no-imap-del") is None
        finally:
            conn.close()

    def test_no_imap_uid_deletes_locally(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "no-uid-del",
                    "sender": "x@x.com",
                    "subject": "No UID",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(single_db, mail_config=mail_config)
        handler.headers.get.return_value = 80
        handler.rfile.read.return_value = b"message_id=no-uid-del&redirect_to=/board"

        handler._handle_delete()

        conn = init_db(single_db)
        try:
            assert get_record_by_message_id(conn, "no-uid-del") is None
        finally:
            conn.close()

    def test_happy_imap_delete(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "happy-imap-del",
                    "sender": "x@x.com",
                    "subject": "Happy IMAP",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        conn = init_db(single_db)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (55, "happy-imap-del"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(single_db, mail_config=mail_config)
        handler.headers.get.return_value = 90
        handler.rfile.read.return_value = (
            b"message_id=happy-imap-del&redirect_to=/board"
        )

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.search_uids.return_value = [55]

            handler._handle_delete()

        mock_client.delete_message.assert_called_once_with(55)
        # Local record removed.
        conn2 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn2, "happy-imap-del") is None
        finally:
            conn2.close()

    def test_imap_not_found_cross_folder_heal(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "cf-heal-del",
                    "sender": "x@x.com",
                    "subject": "Cross-folder heal",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        conn = init_db(single_db)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ?, source_folder = ? "
                "WHERE message_id = ?",
                (42, "INBOX", "cf-heal-del"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(single_db, mail_config=mail_config)
        handler.headers.get.return_value = 90
        handler.rfile.read.return_value = b"message_id=cf-heal-del&redirect_to=/board"

        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
            mock.patch("robotsix_auto_mail.imap.cross_folder_resolve") as mock_cross,
        ):
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.search_uids.return_value = []
            mock_cross.return_value = ("Projects", 99)

            handler._handle_delete()

        # The second client should have called delete_message with the
        # healed UID.
        mock_client.delete_message.assert_called_once_with(99)

        # Local record removed.
        conn2 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn2, "cf-heal-del") is None
        finally:
            conn2.close()

    def test_imap_not_found_cross_folder_heal_failure_502(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "cf-heal-fail",
                    "sender": "x@x.com",
                    "subject": "Heal fail",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        conn = init_db(single_db)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (42, "cf-heal-fail"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(single_db, mail_config=mail_config)
        handler.headers.get.return_value = 95
        handler.rfile.read.return_value = b"message_id=cf-heal-fail&redirect_to=/board"

        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
            mock.patch("robotsix_auto_mail.imap.cross_folder_resolve") as mock_cross,
        ):
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.search_uids.return_value = []
            mock_cross.side_effect = ImapError("connection lost")

            handler._handle_delete()

        handler._send_response.assert_called_once()
        call_args = handler._send_response.call_args
        assert call_args[1]["status"] == 502

        # Local record preserved.
        conn2 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn2, "cf-heal-fail") is not None
        finally:
            conn2.close()

    def test_imap_error_returns_502(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "imap-err-del",
                    "sender": "x@x.com",
                    "subject": "IMAP error",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        conn = init_db(single_db)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (10, "imap-err-del"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(single_db, mail_config=mail_config)
        handler.headers.get.return_value = 85
        handler.rfile.read.return_value = b"message_id=imap-err-del&redirect_to=/board"

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            # Make the context manager itself raise ImapError on enter.
            mock_cls.side_effect = ImapError("connection refused")

            handler._handle_delete()

        handler._send_response.assert_called_once()
        call_args = handler._send_response.call_args
        assert call_args[1]["status"] == 502

        # Local record preserved.
        conn2 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn2, "imap-err-del") is not None
        finally:
            conn2.close()
