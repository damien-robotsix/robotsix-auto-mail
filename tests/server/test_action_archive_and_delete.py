"""Tests for ``_BoardActionMixin._archive_and_delete``.

Verifies IMAP archive-and-delete behaviour including happy-path local deletion
after IMAP move, error handling (ValueError → 400, ImapError → 502), stale-UID
cross-folder healing, archive subfolder recording, and no-IMAP-config fallback.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import get_record_by_message_id, init_db
from robotsix_auto_mail.imap import ImapError
from robotsix_auto_mail.triage import TO_ARCHIVE
from tests.server._test_helpers import _FakeHandler
from tests.server.conftest_helpers import _populate_db, _seed_archive_override


@pytest.fixture(autouse=True)
def record_user_action_mock() -> Iterator[mock.MagicMock]:
    """Patch ``record_user_action`` so no background flash-LLM thread runs.

    The move/archive handlers call ``record_user_action`` to update the
    triage-rules file via a background daemon thread.  Patch it out so tests
    stay deterministic and never touch the network.
    """
    with mock.patch(
        "robotsix_auto_mail.server._action_mixin.record_user_action"
    ) as patched:
        yield patched


class TestArchiveAndDelete:
    def test_happy_path_deletes_local_after_imap_move(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-rec",
                    "sender": "x@x.com",
                    "subject": "Archive me",
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
                (7, "arch-rec"),
            )
            conn.commit()
            record = get_record_by_message_id(conn, "arch-rec")
            assert record is not None
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="my-archive",
        )
        handler = _FakeHandler(single_db, mail_config=mail_config)
        # Re-open: the mixin methods use their own connections for some
        # operations but _archive_and_delete receives conn as an arg.
        conn2 = init_db(single_db)
        try:
            record2 = get_record_by_message_id(conn2, "arch-rec")
            assert record2 is not None

            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]
                mock_client.search_uids.return_value = [7]

                result = handler._archive_and_delete(conn2, record2)

            assert result is True
        finally:
            conn2.close()

        # Local record deleted.
        conn3 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn3, "arch-rec") is None
        finally:
            conn3.close()

    def test_value_error_returns_400_preserves_record(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-rec",
                    "sender": "x@x.com",
                    "subject": "Archive me",
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
                (7, "arch-rec"),
            )
            conn.commit()
            record = get_record_by_message_id(conn, "arch-rec")
            assert record is not None

            mail_config = MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="test",
                password="test",
                archive_root="my-archive",
            )
            handler = _FakeHandler(single_db, mail_config=mail_config)

            with (
                mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
                mock.patch(
                    "robotsix_auto_mail.server.adapters._archive_dest_folder",
                    return_value=None,
                ),
            ):
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

                result = handler._archive_and_delete(conn, record)

            assert result is False
            handler._bad_request.assert_called_once()
        finally:
            conn.close()

        # Local record preserved.
        conn2 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn2, "arch-rec") is not None
        finally:
            conn2.close()

    def test_imap_error_returns_502_preserves_record(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-rec",
                    "sender": "x@x.com",
                    "subject": "Archive me",
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
                (7, "arch-rec"),
            )
            conn.commit()
            record = get_record_by_message_id(conn, "arch-rec")
            assert record is not None

            mail_config = MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="test",
                password="test",
                archive_root="my-archive",
            )
            handler = _FakeHandler(single_db, mail_config=mail_config)

            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_cls.side_effect = ImapError("connection refused")

                result = handler._archive_and_delete(conn, record)

            assert result is False
            handler._send_response.assert_called_once()
            assert handler._send_response.call_args[1]["status"] == 502
        finally:
            conn.close()

        # Local record preserved.
        conn2 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn2, "arch-rec") is not None
        finally:
            conn2.close()

    def test_stale_uid_cross_folder_heal_and_delete(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-rec",
                    "sender": "x@x.com",
                    "subject": "Archive me",
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
                (42, "INBOX", "arch-rec"),
            )
            conn.commit()
            record = get_record_by_message_id(conn, "arch-rec")
            assert record is not None

            mail_config = MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="test",
                password="test",
                archive_root="my-archive",
            )
            handler = _FakeHandler(single_db, mail_config=mail_config)

            with (
                mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
                mock.patch(
                    "robotsix_auto_mail.imap.cross_folder_resolve"
                ) as mock_cross,
            ):
                mock_client = mock_cls.return_value.__enter__.return_value
                # Outer _imap_archive_move fails (stale UID 42),
                # triggering the cross-folder fallback.  The inner
                # _imap_archive_move must succeed — resolve the
                # healed UID 99.
                mock_client.search_uids.side_effect = lambda q: (
                    [99] if "99" in q else []
                )
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]
                mock_cross.return_value = ("Projects", 99)

                result = handler._archive_and_delete(conn, record)

            assert result is True
            # Verify that the healed UID was moved (by the inner
            # _imap_archive_move call, not the original duplicated
            # move logic).
            mock_client.move_message.assert_called_once()
            move_uid = mock_client.move_message.call_args[0][0]
            assert move_uid == 99
        finally:
            conn.close()

        # Local record deleted.
        conn2 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn2, "arch-rec") is None
        finally:
            conn2.close()

    def test_records_user_action_before_delete(
        self, single_db: str, record_user_action_mock: mock.MagicMock
    ) -> None:
        """When a subfolder is chosen, ``record_user_action`` is called with
        that subfolder before the local row is deleted."""
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-rec",
                    "sender": "x@x.com",
                    "subject": "Archive me",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_archive_override(single_db, "arch-rec", "Lists/new-list")
        conn = init_db(single_db)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (7, "arch-rec"),
            )
            conn.commit()
            record = get_record_by_message_id(conn, "arch-rec")
            assert record is not None

            mail_config = MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="test",
                password="test",
                archive_root="my-archive",
            )
            handler = _FakeHandler(single_db, mail_config=mail_config)

            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]
                mock_client.search_uids.return_value = [7]

                result = handler._archive_and_delete(conn, record)

            assert result is True
            # record_user_action should have been called with the record,
            # the TO_ARCHIVE action, and the chosen subfolder.
            mock_record = record_user_action_mock
            mock_record.assert_called_once()
            call_args = mock_record.call_args
            assert call_args[0][0].message_id == "arch-rec"
            assert call_args[0][1] == TO_ARCHIVE
            assert call_args[1]["subfolder"] == "Lists/new-list"
        finally:
            conn.close()

    def test_no_imap_config_local_delete_only(self, single_db: str) -> None:
        """When mail_config is None, only local deletion happens."""
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-rec",
                    "sender": "x@x.com",
                    "subject": "Archive me",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        conn = init_db(single_db)
        try:
            record = get_record_by_message_id(conn, "arch-rec")
            assert record is not None

            handler = _FakeHandler(single_db, mail_config=None)
            result = handler._archive_and_delete(conn, record)

            assert result is True
        finally:
            conn.close()

        conn2 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn2, "arch-rec") is None
        finally:
            conn2.close()
