"""Tests for archive namespace prefix, source-folder selection, and save-notes."""

from __future__ import annotations

from unittest import mock

from tests.server.conftest import (
    _populate_db,
    _post_to_path,
    _seed_archive_override,
    _seed_triage_decision,
    _start_test_server,
    _start_test_server_with_mail_config,
)

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import init_db

# ---------------------------------------------------------------------------
# _handle_archive — namespace prefix
# ---------------------------------------------------------------------------


def test_archive_namespace_creates_folders_with_prefix(single_db: str) -> None:
    """With archive_namespace set, folders are created under the
    namespaced effective root."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "ns-mid",
                "sender": "x@x.com",
                "subject": "ns",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "ns-mid", action="TO_ARCHIVE")
    _seed_archive_override(single_db, "ns-mid", "Lists/new-list")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (99, "ns-mid"),
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
        archive_namespace="INBOX.",
    )

    server, port = _start_test_server_with_mail_config(single_db, mail_config)
    try:
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

            resp = _post_to_path(port, "/archive", {"message_id": "ns-mid"})

        assert resp.status == 302

        expected_calls = [
            mock.call("INBOX.my-archive"),
            mock.call("INBOX.my-archive/Lists"),
            mock.call("INBOX.my-archive/Lists/new-list"),
        ]
        assert mock_client.create_folder.call_args_list == expected_calls
        mock_client.move_message.assert_called_once_with(
            99, "INBOX.my-archive/Lists/new-list"
        )
    finally:
        server.shutdown()


def test_archive_namespace_security_gate_uses_effective_root(single_db: str) -> None:
    """The security gate checks against the effective (namespaced) root."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "ns-safe-mid",
                "sender": "x@x.com",
                "subject": "ns-safe",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "ns-safe-mid", action="TO_ARCHIVE")
    _seed_archive_override(single_db, "ns-safe-mid", "Lists/ok")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (88, "ns-safe-mid"),
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
        archive_namespace="INBOX.",
    )

    server, port = _start_test_server_with_mail_config(single_db, mail_config)
    try:
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

            resp = _post_to_path(port, "/archive", {"message_id": "ns-safe-mid"})

        # The effective root is "INBOX.my-archive" and the dest
        # is "INBOX.my-archive/Lists/ok" — starts-with check passes.
        assert resp.status == 302
        mock_client.move_message.assert_called_once()
    finally:
        server.shutdown()


def test_archive_selects_source_folder_not_just_inbox(single_db: str) -> None:
    """POST /archive on a record whose source_folder is not INBOX selects
    the record's source_folder instead of the default IMAP folder."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "legacy-arch",
                "sender": "x@x.com",
                "subject": "Legacy archive",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "legacy-arch", action="TO_ARCHIVE")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ?, source_folder = ? "
            "WHERE message_id = ?",
            (99, "INBOX.archive", "legacy-arch"),
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
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]
            # search_uids: UID 99 exists in INBOX.archive.
            mock_client.search_uids.return_value = [99]

            resp = _post_to_path(port, "/archive", {"message_id": "legacy-arch"})

        assert resp.status == 302
        # The record's source_folder ("INBOX.archive") must have
        # been selected — NOT the default "INBOX".
        select_calls = [c.args[0] for c in mock_client.select_folder.call_args_list]
        assert "INBOX.archive" in select_calls, (
            f"Expected select_folder('INBOX.archive'), got {select_calls}"
        )
        mock_client.move_message.assert_called_once()
    finally:
        server.shutdown()


def test_archive_message_id_fallback_when_uid_stale(single_db: str) -> None:
    """POST /archive: when the stored UID is stale, the Message-ID fallback
    finds the message and the archive succeeds."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "fallback-arch",
                "sender": "x@x.com",
                "subject": "Fallback archive",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "fallback-arch", action="TO_ARCHIVE")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (42, "fallback-arch"),
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
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

            # UID 42 is stale → search returns [].
            # But the Message-ID fallback finds UID 77.
            call_count = [0]

            def _search_uids(criteria: str) -> list[int]:
                call_count[0] += 1
                if "UID 42" in criteria:
                    return []  # stale
                if "fallback-arch" in criteria:
                    return [77]  # found via Message-ID
                return [42]  # default

            mock_client.search_uids.side_effect = _search_uids

            resp = _post_to_path(port, "/archive", {"message_id": "fallback-arch"})

        assert resp.status == 302, (
            f"Expected 302, got {resp.status}: {resp.read().decode()[:200]}"
        )
        # The move must use the resolved UID 77, not the stale 42.
        mock_client.move_message.assert_called_once()
        move_uid = mock_client.move_message.call_args[0][0]
        assert move_uid == 77, (
            f"Expected move_message with UID 77 (resolved), got {move_uid}"
        )
    finally:
        server.shutdown()


def test_save_notes_persists_and_redirects(single_db: str) -> None:
    """POST /save-notes with message_id and notes persists and returns 302."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "notes-test-1",
                "sender": "x@x.com",
                "subject": "Notes test",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(
            port,
            "/save-notes",
            {
                "message_id": "notes-test-1",
                "notes": "Waiting for Alice's feedback",
            },
        )
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"
    finally:
        server.shutdown()

    # Verify notes persisted in DB.
    from robotsix_auto_mail.db import get_record_by_message_id, init_db

    conn = init_db(single_db)
    try:
        record = get_record_by_message_id(conn, "notes-test-1")
        assert record is not None
        assert record.notes == "Waiting for Alice's feedback"
    finally:
        conn.close()


def test_save_notes_nonexistent_message_id_returns_404() -> None:
    """POST /save-notes with nonexistent message_id returns 404."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(
            port,
            "/save-notes",
            {"message_id": "does-not-exist", "notes": "whatever"},
        )
        assert resp.status == 404
    finally:
        server.shutdown()


def test_save_notes_empty_message_id_returns_400() -> None:
    """POST /save-notes with empty message_id returns 400."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(port, "/save-notes", {"message_id": "  ", "notes": "x"})
        assert resp.status == 400
    finally:
        server.shutdown()


def test_save_notes_missing_message_id_returns_400() -> None:
    """POST /save-notes without message_id returns 400."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(port, "/save-notes", {"notes": "x"})
        assert resp.status == 400
    finally:
        server.shutdown()
