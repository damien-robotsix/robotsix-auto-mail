"""Tests for cross-folder healing and transient IMAP errors during delete
and archive operations."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import get_record_by_message_id, init_db
from robotsix_auto_mail.imap import ImapError
from tests.server.conftest_helpers import (
    _populate_db,
    _post_form,
    _seed_triage_decision,
    _start_test_server_with_mail_config,
)


def test_delete_cross_folder_heal_and_delete(single_db: str) -> None:
    """POST /delete with a stale UID where cross-folder search finds the
    mail in another folder → heal record, IMAP-delete from new location,
    remove local record, 302."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "heal-del",
                "sender": "x@x.com",
                "subject": "Heal delete",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "heal-del", action="TO_DELETE")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ?, source_folder = ? "
            "WHERE message_id = ?",
            (42, "INBOX", "heal-del"),
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

    server, port = _start_test_server_with_mail_config(single_db, mail_config)
    try:
        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
            mock.patch("robotsix_auto_mail.imap.cross_folder_resolve") as mock_cross,
        ):
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.search_uids.return_value = []
            mock_cross.return_value = ("Projects", 99)

            status, body = _post_form(port, {"message_id": "heal-del"}, path="/delete")

        assert status == 302, f"Expected 302, got {status}: {body}"
        # Verify the delete was called with the healed UID.
        mock_client.delete_message.assert_called_once_with(99)
    finally:
        server.shutdown()

    # The local record must be removed.
    conn = init_db(single_db)
    try:
        assert get_record_by_message_id(conn, "heal-del") is None
    finally:
        conn.close()


def test_delete_transient_imap_error_preserves_record(single_db: str) -> None:
    """POST /delete with a stale UID where cross-folder search raises
    ImapError → 502, local record preserved."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "transient-del",
                "sender": "x@x.com",
                "subject": "Transient delete",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "transient-del", action="TO_DELETE")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (42, "transient-del"),
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

    server, port = _start_test_server_with_mail_config(single_db, mail_config)
    try:
        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
            mock.patch("robotsix_auto_mail.imap.cross_folder_resolve") as mock_cross,
        ):
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.search_uids.return_value = []
            mock_cross.side_effect = ImapError("connection lost")

            status, body = _post_form(
                port, {"message_id": "transient-del"}, path="/delete"
            )

        assert status == 502, f"Expected 502, got {status}: {body}"
    finally:
        server.shutdown()

    # The local record must remain intact.
    conn = init_db(single_db)
    try:
        assert get_record_by_message_id(conn, "transient-del") is not None
    finally:
        conn.close()


def test_archive_cross_folder_heal_and_archive(single_db: str) -> None:
    """POST /archive with a stale UID where cross-folder search finds the
    mail in another folder → heal record, IMAP-move to archive from new
    location, remove local record, 302."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "heal-arch",
                "sender": "x@x.com",
                "subject": "Heal archive",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "heal-arch", action="TO_ARCHIVE")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ?, source_folder = ? "
            "WHERE message_id = ?",
            (42, "INBOX", "heal-arch"),
        )
        conn.commit()
    finally:
        conn.close()

    mail_config = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="test",
        password="test",
        archive_root="my-archive",
    )

    server, port = _start_test_server_with_mail_config(single_db, mail_config)
    try:
        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
            mock.patch("robotsix_auto_mail.imap.cross_folder_resolve") as mock_cross,
        ):
            mock_client = mock_cls.return_value.__enter__.return_value
            # Outer _imap_archive_move fails (stale UID 42),
            # triggering the cross-folder fallback.  The inner
            # _imap_archive_move must succeed — resolve the
            # healed UID 99.
            mock_client.search_uids.side_effect = lambda q: [99] if "99" in q else []
            mock_cross.return_value = ("Projects", 99)
            # The archive heal path calls list_folders on the
            # second client to get the delimiter.
            mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

            status, body = _post_form(
                port, {"message_id": "heal-arch"}, path="/archive"
            )

        assert status == 302, f"Expected 302, got {status}: {body}"
        # Verify the move was called with the healed UID.
        mock_client.move_message.assert_called_once()
        move_uid = mock_client.move_message.call_args[0][0]
        assert move_uid == 99, (
            f"Expected move_message with UID 99 (healed), got {move_uid}"
        )
    finally:
        server.shutdown()

    # The local record must be removed.
    conn = init_db(single_db)
    try:
        assert get_record_by_message_id(conn, "heal-arch") is None
    finally:
        conn.close()
