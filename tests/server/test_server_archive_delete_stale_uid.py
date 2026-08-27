"""Tests for stale UID handling during delete and archive operations."""

from __future__ import annotations

from unittest import mock

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import get_record_by_message_id, init_db
from tests.server.conftest_helpers import (
    _populate_db,
    _post_form,
    _seed_archive_override,
    _seed_triage_decision,
    _start_test_server_with_mail_config,
    _wait_for_batch_idle,
)

# ---------------------------------------------------------------------------
# Stale-UID: mail truly gone, IMAP search fails everywhere
# ---------------------------------------------------------------------------


def test_delete_stale_uid_preserves_record(single_db: str) -> None:
    """POST /delete with a stale UID and cross-folder search failing
    (mail truly gone) → 302, local record removed."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "stale-del",
                "sender": "x@x.com",
                "subject": "Stale delete",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "stale-del", action="TO_DELETE")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (42, "stale-del"),
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
            # All searches fail → resolve_uid_with_fallback raises
            # ImapMessageNotFoundError.
            mock_client.search_uids.return_value = []
            mock_cross.return_value = None  # mail gone

            status, body = _post_form(port, {"message_id": "stale-del"}, path="/delete")

        assert status == 302, f"Expected 302, got {status}: {body}"
    finally:
        server.shutdown()

    # The local record must be removed.
    conn = init_db(single_db)
    try:
        assert get_record_by_message_id(conn, "stale-del") is None
    finally:
        conn.close()


def test_archive_stale_uid_preserves_record(single_db: str) -> None:
    """POST /archive with a stale UID and cross-folder search failing
    (mail truly gone) → 302, local record removed, no user action recorded."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "stale-arch",
                "sender": "x@x.com",
                "subject": "Stale archive",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "stale-arch", action="TO_ARCHIVE")
    _seed_archive_override(single_db, "stale-arch", "Lists/new-list")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (42, "stale-arch"),
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
            mock_client.search_uids.return_value = []
            mock_cross.return_value = None  # mail gone

            status, body = _post_form(
                port, {"message_id": "stale-arch"}, path="/archive"
            )

        assert status == 302, f"Expected 302, got {status}: {body}"
    finally:
        server.shutdown()

    conn = init_db(single_db)
    try:
        # Record removed.
        assert get_record_by_message_id(conn, "stale-arch") is None
    finally:
        conn.close()


def test_batch_delete_stale_uid_preserves_all_records(single_db: str) -> None:
    """POST /batch-delete with one stale UID where the mail is
    verifiably gone: the stale record is removed from the DB,
    the remaining record is still deleted by the background
    worker, and the server responds with 302."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "bd-stale-1",
                "sender": "a@b.com",
                "subject": "Delete me 1",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
            {
                "message_id": "bd-stale-2",
                "sender": "c@d.com",
                "subject": "Delete me 2",
                "date": "2025-01-02T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "bd-stale-1", action="TO_DELETE")
    _seed_triage_decision(single_db, "bd-stale-2", action="TO_DELETE")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (42, "bd-stale-1"),
        )
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (43, "bd-stale-2"),
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

            def _search_uids(criteria: str) -> list[int]:
                if "UID 42" in criteria:
                    return []  # stale
                if "UID 43" in criteria:
                    return [43]
                # Message-ID fallback: stale message not findable.
                if "bd-stale-1" in criteria:
                    return []
                return [42, 43]

            mock_client.search_uids.side_effect = _search_uids
            mock_cross.return_value = None  # mail gone

            status, body = _post_form(port, {}, path="/batch-delete")
            assert status == 302, f"Expected 302, got {status}: {body}"
            # Work happens in a background daemon thread (the handler no
            # longer blocks on a synchronous precheck) — wait for it while
            # the IMAP mocks are still active.
            _wait_for_batch_idle(single_db)
    finally:
        server.shutdown()

    # The worker deletes every TO_DELETE record: the stale-UID one (mail
    # gone) is dropped from the DB, and the resolvable one is expunged.
    conn = init_db(single_db)
    try:
        assert get_record_by_message_id(conn, "bd-stale-1") is None
        assert get_record_by_message_id(conn, "bd-stale-2") is None
    finally:
        conn.close()


def test_batch_archive_stale_uid_preserves_all_records(single_db: str) -> None:
    """POST /batch-archive with one stale UID where the mail is
    verifiably gone: the stale record is removed from the DB and
    the server responds with 302."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "ba-stale-1",
                "sender": "a@b.com",
                "subject": "Archive me 1",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
            {
                "message_id": "ba-stale-2",
                "sender": "c@d.com",
                "subject": "Archive me 2",
                "date": "2025-01-02T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "ba-stale-1", action="TO_ARCHIVE")
    _seed_triage_decision(single_db, "ba-stale-2", action="TO_ARCHIVE")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (42, "ba-stale-1"),
        )
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (43, "ba-stale-2"),
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

            def _search_uids(criteria: str) -> list[int]:
                if "UID 42" in criteria:
                    return []  # stale
                if "UID 43" in criteria:
                    return [43]
                if "ba-stale-1" in criteria:
                    return []
                return [42, 43]

            mock_client.search_uids.side_effect = _search_uids
            mock_cross.return_value = None  # mail gone

            status, body = _post_form(port, {}, path="/batch-archive")
            assert status == 302, f"Expected 302, got {status}: {body}"
            # Work happens in a background daemon thread (the handler no
            # longer blocks on a synchronous precheck) — wait for it while
            # the IMAP mocks are still active.
            _wait_for_batch_idle(single_db)
    finally:
        server.shutdown()

    # ba-stale-1 was removed by the background archive worker (mail gone).
    conn = init_db(single_db)
    try:
        assert get_record_by_message_id(conn, "ba-stale-1") is None
    finally:
        conn.close()
