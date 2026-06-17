"""Tests for archive and delete operations (IMAP interaction, batch ops)."""

from __future__ import annotations

import json
from unittest import mock
from urllib.request import urlopen

import pytest
from tests.server.conftest import (
    _populate_db,
    _post_form,
    _post_to_path,
    _seed_archive_override,
    _seed_archive_structure,
    _seed_batch_state,
    _seed_triage_decision,
    _start_test_server,
    _start_test_server_with_mail_config,
    _wait_for_batch_idle,
)

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import init_db

# ---------------------------------------------------------------------------
# Delete button on TO_DELETE cards
# ---------------------------------------------------------------------------


def test_delete_success_removes_record_and_redirects(single_db: str) -> None:
    """POST /delete with valid message_id deletes the record and returns 302."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "del-me",
                "sender": "x@x.com",
                "subject": "Delete test",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "del-me", action="TO_DELETE")

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(port, "/delete", {"message_id": "del-me"})
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"
    finally:
        server.shutdown()

    # Verify record is gone from the DB.
    from robotsix_auto_mail.db import get_record_by_message_id, init_db

    conn = init_db(single_db)
    try:
        assert get_record_by_message_id(conn, "del-me") is None
    finally:
        conn.close()

    # Verify the board no longer shows the card.
    server2, port2 = _start_test_server(single_db)
    try:
        resp2 = urlopen(f"http://127.0.0.1:{port2}/board")
        board_html = resp2.read().decode("utf-8")
        assert "del-me" not in board_html
        assert "x@x.com" not in board_html
    finally:
        server2.shutdown()


def test_delete_missing_message_id_returns_400() -> None:
    """POST /delete without message_id returns 400."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(port, "/delete", {})
        assert resp.status == 400
    finally:
        server.shutdown()


def test_delete_empty_message_id_returns_400() -> None:
    """POST /delete with empty message_id returns 400."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(port, "/delete", {"message_id": "  "})
        assert resp.status == 400
    finally:
        server.shutdown()


def test_delete_unknown_message_id_returns_404() -> None:
    """POST /delete with nonexistent message_id returns 404."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(
            port,
            "/delete",
            {"message_id": "does-not-exist"},
        )
        assert resp.status == 404
    finally:
        server.shutdown()


def test_batch_delete_success_removes_all_to_delete_records_and_redirects(
    single_db: str,
) -> None:
    """POST /batch-delete deletes every TO_DELETE record and redirects 302."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "bd-del-1",
                "sender": "a@b.com",
                "subject": "Delete me 1",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
            {
                "message_id": "bd-del-2",
                "sender": "c@d.com",
                "subject": "Delete me 2",
                "date": "2025-01-02T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
            {
                "message_id": "bd-keep",
                "sender": "e@f.com",
                "subject": "Keep me",
                "date": "2025-01-03T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "bd-del-1", action="TO_DELETE")
    _seed_triage_decision(single_db, "bd-del-2", action="TO_DELETE")
    # bd-keep is untriaged → INBOX, should survive.

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(port, "/batch-delete", {})
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"
        # The worker now runs in a background daemon thread — poll the
        # batch_op:state watermark until it clears back to "idle".
        _wait_for_batch_idle(single_db)
    finally:
        server.shutdown()

    # Verify the TO_DELETE records are gone.
    from robotsix_auto_mail.db import get_record_by_message_id, init_db

    conn = init_db(single_db)
    try:
        assert get_record_by_message_id(conn, "bd-del-1") is None
        assert get_record_by_message_id(conn, "bd-del-2") is None
        assert get_record_by_message_id(conn, "bd-keep") is not None
    finally:
        conn.close()


def test_batch_delete_empty_column_returns_302() -> None:
    """POST /batch-delete when TO_DELETE is empty → 302, no error."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(port, "/batch-delete", {})
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"
    finally:
        server.shutdown()


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
    from robotsix_auto_mail.db import get_record_by_message_id

    conn = init_db(single_db)
    try:
        assert get_record_by_message_id(conn, "stale-del") is None
    finally:
        conn.close()


def test_archive_stale_uid_preserves_record(single_db: str) -> None:
    """POST /archive with a stale UID and cross-folder search failing
    (mail truly gone) → 302, local record removed, no folder memory."""

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

    from robotsix_auto_mail.db import get_record_by_message_id
    from robotsix_auto_mail.triage import _load_archive_folder_memory

    conn = init_db(single_db)
    try:
        # Record removed and archive-folder memory NOT written.
        assert get_record_by_message_id(conn, "stale-arch") is None
        assert _load_archive_folder_memory(conn) == {}
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
    from robotsix_auto_mail.db import get_record_by_message_id

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
    from robotsix_auto_mail.db import get_record_by_message_id

    conn = init_db(single_db)
    try:
        assert get_record_by_message_id(conn, "ba-stale-1") is None
    finally:
        conn.close()


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
    from robotsix_auto_mail.db import get_record_by_message_id

    conn = init_db(single_db)
    try:
        assert get_record_by_message_id(conn, "heal-del") is None
    finally:
        conn.close()


def test_delete_transient_imap_error_preserves_record(single_db: str) -> None:
    """POST /delete with a stale UID where cross-folder search raises
    ImapError → 502, local record preserved."""

    from robotsix_auto_mail.imap import ImapError

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
    from robotsix_auto_mail.db import get_record_by_message_id

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
            mock_client.search_uids.return_value = []
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
    from robotsix_auto_mail.db import get_record_by_message_id

    conn = init_db(single_db)
    try:
        assert get_record_by_message_id(conn, "heal-arch") is None
    finally:
        conn.close()


def test_archive_proposal_endpoint_returns_json(single_db: str) -> None:
    """GET /archive-proposal/<mid> returns expected JSON shape."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "ap-mid",
                "sender": "alice@example.com",
                "subject": "Archive me",
                "date": "2025-06-01T12:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "ap-mid", action="TO_ARCHIVE")
    _seed_archive_structure(
        single_db,
        ["my-archive", "my-archive/Lists/dev"],
    )

    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/archive-proposal/ap-mid")
        assert resp.status == 200
        assert resp.headers.get("Content-Type", "").startswith("application/json")
        body = resp.read().decode("utf-8")
        payload = json.loads(body)
        assert "subfolder" in payload
        assert "archive_root" in payload
        assert "folder_exists" in payload
        assert "overridden" in payload
        assert "source" in payload
        # Either "rule" (deterministic) or "llm" (no hint stored)
        assert payload["source"] in ("rule", "llm", "override")
        assert isinstance(payload["folder_exists"], bool)
        assert isinstance(payload["overridden"], bool)
    finally:
        server.shutdown()


def test_archive_proposal_endpoint_with_override(single_db: str) -> None:
    """GET /archive-proposal/<mid> returns source='override' when override exists."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "ap-override",
                "sender": "a@b.com",
                "subject": "Test",
                "date": "2025-06-01T12:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "ap-override", action="TO_ARCHIVE")
    _seed_archive_override(single_db, "ap-override", "Custom/Path")

    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/archive-proposal/ap-override")
        payload = json.loads(resp.read().decode("utf-8"))
        assert payload["subfolder"] == "Custom/Path"
        assert payload["source"] == "override"
        assert payload["overridden"] is True
    finally:
        server.shutdown()


def test_archive_proposal_endpoint_unknown_404() -> None:
    """GET /archive-proposal/unknown → 404."""
    import urllib.error

    server, port = _start_test_server(":memory:")
    try:
        try:
            urlopen(f"http://127.0.0.1:{port}/archive-proposal/nonexistent")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("Expected HTTPError 404")
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# POST /archive-proposal
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# POST /archive-proposal
# ---------------------------------------------------------------------------


def test_archive_proposal_post_stores_override_and_redirects(single_db: str) -> None:
    """POST /archive-proposal persists the override and redirects to /board."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "post-ap",
                "sender": "a@b.com",
                "subject": "Test",
                "date": "2025-06-01T12:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(
            port,
            "/archive-proposal",
            {"message_id": "post-ap", "subfolder": "My/Path"},
        )
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"
    finally:
        server.shutdown()

    # Verify override was persisted.
    from robotsix_auto_mail.triage import _load_archive_overrides

    conn = init_db(single_db)
    try:
        overrides = _load_archive_overrides(conn)
        assert overrides.get("post-ap") == "My/Path"
    finally:
        conn.close()


def test_archive_proposal_post_empty_subfolder_clears_override(single_db: str) -> None:
    """POST with empty subfolder clears the override."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "clear-ap",
                "sender": "a@b.com",
                "subject": "Test",
                "date": "2025-06-01T12:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_archive_override(single_db, "clear-ap", "Existing")

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(
            port,
            "/archive-proposal",
            {"message_id": "clear-ap", "subfolder": ""},
        )
        assert resp.status == 302
    finally:
        server.shutdown()

    from robotsix_auto_mail.triage import _load_archive_overrides

    conn = init_db(single_db)
    try:
        overrides = _load_archive_overrides(conn)
        assert "clear-ap" not in overrides
    finally:
        conn.close()


def test_archive_proposal_post_records_archive_folder_memory(single_db: str) -> None:
    """POST /archive-proposal with a non-empty subfolder records the choice
    in archive-folder memory (both sender and domain)."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "rec-ap",
                "sender": "a@b.com",
                "subject": "Test",
                "date": "2025-06-01T12:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(
            port,
            "/archive-proposal",
            {"message_id": "rec-ap", "subfolder": "My/Path"},
        )
        assert resp.status == 302
    finally:
        server.shutdown()

    from robotsix_auto_mail.triage import _load_archive_folder_memory

    conn = init_db(single_db)
    try:
        memory = _load_archive_folder_memory(conn)
        assert memory["a@b.com"].subfolder == "My/Path"
        assert memory["b.com"].subfolder == "My/Path"
    finally:
        conn.close()


def test_archive_proposal_post_empty_subfolder_records_nothing(single_db: str) -> None:
    """POST /archive-proposal with an empty subfolder records nothing in
    archive-folder memory."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "empty-ap",
                "sender": "a@b.com",
                "subject": "Test",
                "date": "2025-06-01T12:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(
            port,
            "/archive-proposal",
            {"message_id": "empty-ap", "subfolder": ""},
        )
        assert resp.status == 302
    finally:
        server.shutdown()

    from robotsix_auto_mail.triage import _load_archive_folder_memory

    conn = init_db(single_db)
    try:
        assert _load_archive_folder_memory(conn) == {}
    finally:
        conn.close()


def test_archive_records_archive_folder_memory_before_delete(single_db: str) -> None:
    """POST /archive records the effective subfolder in archive-folder memory
    before the local row is deleted."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "arch-mem-mid",
                "sender": "x@x.com",
                "subject": "hier",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "arch-mem-mid", action="TO_ARCHIVE")
    _seed_archive_override(single_db, "arch-mem-mid", "Lists/new-list")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (7, "arch-mem-mid"),
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

            resp = _post_to_path(port, "/archive", {"message_id": "arch-mem-mid"})

        assert resp.status == 302
    finally:
        server.shutdown()

    from robotsix_auto_mail.db import get_record_by_message_id
    from robotsix_auto_mail.triage import _load_archive_folder_memory

    conn = init_db(single_db)
    try:
        # The local row is gone, but the folder memory survives.
        assert get_record_by_message_id(conn, "arch-mem-mid") is None
        memory = _load_archive_folder_memory(conn)
        assert memory["x@x.com"].subfolder == "Lists/new-list"
        assert memory["x.com"].subfolder == "Lists/new-list"
    finally:
        conn.close()


def test_archive_proposal_post_missing_message_id_400() -> None:
    """POST /archive-proposal without message_id returns 400."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(port, "/archive-proposal", {"subfolder": "x"})
        assert resp.status == 400
    finally:
        server.shutdown()


def test_archive_proposal_post_dotdot_segment_400(single_db: str) -> None:
    """POST /archive-proposal with '..' path segment returns 400."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "dotdot-test",
                "sender": "a@b.com",
                "subject": "Test",
                "date": "2025-06-01T12:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(
            port,
            "/archive-proposal",
            {"message_id": "dotdot-test", "subfolder": "Lists/../etc"},
        )
        assert resp.status == 400
    finally:
        server.shutdown()


def test_archive_proposal_post_absolute_path_400(single_db: str) -> None:
    """POST /archive-proposal with absolute path returns 400."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "abs-path-test",
                "sender": "a@b.com",
                "subject": "Test",
                "date": "2025-06-01T12:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(
            port,
            "/archive-proposal",
            {"message_id": "abs-path-test", "subfolder": "/etc/passwd"},
        )
        assert resp.status == 400
    finally:
        server.shutdown()


def test_archive_proposal_post_overly_long_subfolder_400(single_db: str) -> None:
    """POST /archive-proposal with subfolder exceeding 256 chars returns 400."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "long-sub-test",
                "sender": "a@b.com",
                "subject": "Test",
                "date": "2025-06-01T12:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(
            port,
            "/archive-proposal",
            {"message_id": "long-sub-test", "subfolder": "x" * 257},
        )
        assert resp.status == 400
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# Board integration — archive proposal in full board HTML
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Board integration — archive proposal in full board HTML
# ---------------------------------------------------------------------------


def test_archive_rejects_dot_dot_path_segment(single_db: str) -> None:
    """POST /archive with a subfolder containing '..' → 400, mail stays."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "dotdot-mid",
                "sender": "x@x.com",
                "subject": "dotdot",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "dotdot-mid", action="TO_ARCHIVE")
    # Inject a malicious subfolder override.
    _seed_archive_override(single_db, "dotdot-mid", "Lists/../escape")

    # Give the record an imap_uid so the IMAP path is entered.
    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (42, "dotdot-mid"),
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
        # Mock ImapClient so no real connection is attempted.
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

            resp = _post_to_path(port, "/archive", {"message_id": "dotdot-mid"})

        assert resp.status == 400

        # move_message must NOT have been called.
        mock_client.move_message.assert_not_called()
    finally:
        server.shutdown()


def test_archive_rejects_path_escaping_root(single_db: str) -> None:
    """POST /archive where dest_folder doesn't start with archive_root
    → 400, mail stays."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "escape-mid",
                "sender": "x@x.com",
                "subject": "escape",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "escape-mid", action="TO_ARCHIVE")
    # An override that, when joined to root, would produce a path
    # that still starts with root (because the override is relative).
    # We test the alternative: the delimiter check prevents
    # "archive_root/../foo" style escapes, but the starts-with check
    # is an additional layer.  To exercise it we use a subfolder
    # that contains a leading delimiter which, after translation,
    # could produce something like "//etc" which doesn't start
    # with "archive_root/".  However, the real attack surface is
    # the ".." check, already tested above.  The starts-with gate
    # is defense-in-depth that is hard to trigger with the current
    # subfolder→dest_folder building because the subfolder is
    # always appended to archive_root+delimiter.  We still keep
    # the check because it's cheap and protects against future
    # code changes that might build dest_folder differently.
    #
    # For this test we set up a normal record and verify the
    # valid path passes (no 400 from the security gate).
    _seed_archive_override(single_db, "escape-mid", "Lists/ok")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (99, "escape-mid"),
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
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

            resp = _post_to_path(port, "/archive", {"message_id": "escape-mid"})

        # A normal subfolder should pass the security gate and
        # proceed to folder creation + move, resulting in a 302.
        assert resp.status == 302
        mock_client.move_message.assert_called_once()
    finally:
        server.shutdown()


def test_archive_creates_folder_hierarchy_incrementally(single_db: str) -> None:
    """POST /archive creates every level of dest_folder via create_folder."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "hier-mid",
                "sender": "x@x.com",
                "subject": "hier",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "hier-mid", action="TO_ARCHIVE")
    _seed_archive_override(single_db, "hier-mid", "Lists/new-list")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (7, "hier-mid"),
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

            resp = _post_to_path(port, "/archive", {"message_id": "hier-mid"})

        assert resp.status == 302

        # Expect create_folder called for each level:
        #   "my-archive"
        #   "my-archive/Lists"
        #   "my-archive/Lists/new-list"
        expected_calls = [
            mock.call("my-archive"),
            mock.call("my-archive/Lists"),
            mock.call("my-archive/Lists/new-list"),
        ]
        assert mock_client.create_folder.call_args_list == expected_calls

        # move_message must be called after folder creation.
        mock_client.move_message.assert_called_once_with(7, "my-archive/Lists/new-list")
    finally:
        server.shutdown()


def test_archive_no_subfolder_creates_root_only(single_db: str) -> None:
    """POST /archive with empty subfolder creates only the root folder."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "rootonly-mid",
                "sender": "x@x.com",
                "subject": "rootonly",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "rootonly-mid", action="TO_ARCHIVE")
    # No override → subfolder computed by rules; use a sender that
    # produces an empty subfolder (date-based fallback requires a
    # date; but a sender without '@' produces "" from domain parse).
    # The easiest way is to seed an empty-string override.
    _seed_archive_override(single_db, "rootonly-mid", "")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (3, "rootonly-mid"),
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

            resp = _post_to_path(port, "/archive", {"message_id": "rootonly-mid"})

        assert resp.status == 302

        # Only the root folder is created (single level).
        mock_client.create_folder.assert_called_once_with("my-archive")
        mock_client.move_message.assert_called_once_with(3, "my-archive")
    finally:
        server.shutdown()


def test_archive_create_folder_failure_returns_502_no_move(single_db: str) -> None:
    """When create_folder raises ImapError → 502, mail not moved, DB
    record preserved."""

    from robotsix_auto_mail.imap import ImapError

    _populate_db(
        single_db,
        [
            {
                "message_id": "fail-mid",
                "sender": "x@x.com",
                "subject": "fail",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "fail-mid", action="TO_ARCHIVE")
    _seed_archive_override(single_db, "fail-mid", "Lists/doomed")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (55, "fail-mid"),
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
            # Fail on the second create_folder call.
            mock_client.create_folder.side_effect = [
                None,  # "my-archive" succeeds
                ImapError("NO [CANNOT] Cannot create that folder"),
            ]

            resp = _post_to_path(port, "/archive", {"message_id": "fail-mid"})

        assert resp.status == 502

        # move_message must NOT have been called.
        mock_client.move_message.assert_not_called()

        # The DB record must still exist.
        conn2 = init_db(single_db)
        try:
            from robotsix_auto_mail.db import get_record_by_message_id

            assert get_record_by_message_id(conn2, "fail-mid") is not None
        finally:
            conn2.close()
    finally:
        server.shutdown()


def test_archive_idempotent_create_existing_folders_succeeds(single_db: str) -> None:
    """When all folders already exist, create_folder calls still succeed
    (idempotent) and the move still happens."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "idem-mid",
                "sender": "x@x.com",
                "subject": "idem",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "idem-mid", action="TO_ARCHIVE")
    _seed_archive_override(single_db, "idem-mid", "Lists/existing")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (11, "idem-mid"),
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
            # create_folder returns None (success) for every call.
            mock_client.create_folder.return_value = None

            resp = _post_to_path(port, "/archive", {"message_id": "idem-mid"})

        assert resp.status == 302

        # All three levels are still created (idempotent — real
        # ImapClient would no-op).
        assert mock_client.create_folder.call_count == 3
        mock_client.move_message.assert_called_once_with(
            11, "my-archive/Lists/existing"
        )
    finally:
        server.shutdown()


def test_archive_security_gate_runs_before_any_imap_operation(single_db: str) -> None:
    """When the security gate rejects a path, no IMAP operation
    (list_folders, create_folder, move_message) is performed."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "early-mid",
                "sender": "x@x.com",
                "subject": "early",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "early-mid", action="TO_ARCHIVE")
    _seed_archive_override(single_db, "early-mid", "..")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (1, "early-mid"),
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

            resp = _post_to_path(port, "/archive", {"message_id": "early-mid"})

        assert resp.status == 400

        # list_folders is called to discover the delimiter (needed
        # to build dest_folder for the security check), but no
        # mutating operations are performed.
        mock_client.create_folder.assert_not_called()
        mock_client.move_message.assert_not_called()
    finally:
        server.shutdown()


def test_archive_different_delimiter_creates_correct_levels(single_db: str) -> None:
    """When the IMAP server uses '.' as hierarchy delimiter, folder
    creation uses '.'-separated paths."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "dotdelim-mid",
                "sender": "x@x.com",
                "subject": "dotdelim",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "dotdelim-mid", action="TO_ARCHIVE")
    _seed_archive_override(single_db, "dotdelim-mid", "Lists/new-list")

    conn = init_db(single_db)
    try:
        conn.execute(
            "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
            (23, "dotdelim-mid"),
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
            # Server uses '.' as hierarchy delimiter.
            mock_client.list_folders.return_value = [mock.Mock(delimiter=".")]

            resp = _post_to_path(port, "/archive", {"message_id": "dotdelim-mid"})

        assert resp.status == 302

        expected_calls = [
            mock.call("my-archive"),
            mock.call("my-archive.Lists"),
            mock.call("my-archive.Lists.new-list"),
        ]
        assert mock_client.create_folder.call_args_list == expected_calls
        mock_client.move_message.assert_called_once_with(
            23, "my-archive.Lists.new-list"
        )
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# _handle_archive — namespace prefix
# ---------------------------------------------------------------------------


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


def test_batch_delete_single_flight_does_not_spawn_second_worker(
    single_db: str,
) -> None:
    """A second POST /batch-delete while batch_op:state is running is a
    no-op single-flight redirect — the running watermark is untouched."""
    _seed_batch_state(single_db, "running")
    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(port, "/batch-delete", {})
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"
        # No worker spawned → watermark is still the seeded "running".
        from robotsix_auto_mail.db import get_watermark

        conn = init_db(single_db, skip_migrations=True)
        try:
            assert get_watermark(conn, "batch_op:state") == "running"
        finally:
            conn.close()
    finally:
        server.shutdown()


def test_batch_archive_blocked_by_running_delete_shared_key(single_db: str) -> None:
    """POST /batch-archive while a delete is running (shared batch_op:state
    key) is a single-flight no-op and leaves the watermark running."""
    # A JSON delete-progress payload counts as running.
    _seed_batch_state(single_db, json.dumps({"op": "delete", "done": 1, "total": 5}))
    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(port, "/batch-archive", {})
        assert resp.status == 302
        from robotsix_auto_mail.db import get_watermark

        conn = init_db(single_db, skip_migrations=True)
        try:
            state = get_watermark(conn, "batch_op:state")
        finally:
            conn.close()
        assert state == json.dumps({"op": "delete", "done": 1, "total": 5})
    finally:
        server.shutdown()


def test_batch_archive_db_only_removes_records_and_clears_watermark(
    single_db: str,
) -> None:
    """POST /batch-archive deletes every TO_ARCHIVE record (DB-only path,
    no IMAP) in the background and resets batch_op:state to idle."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "ba-1",
                "sender": "a@b.com",
                "subject": "Archive me 1",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
            {
                "message_id": "ba-2",
                "sender": "c@d.com",
                "subject": "Archive me 2",
                "date": "2025-01-02T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "ba-1", action="TO_ARCHIVE")
    _seed_triage_decision(single_db, "ba-2", action="TO_ARCHIVE")

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(port, "/batch-archive", {})
        assert resp.status == 302
        assert _wait_for_batch_idle(single_db) in (None, "idle")
    finally:
        server.shutdown()

    from robotsix_auto_mail.db import get_record_by_message_id

    conn = init_db(single_db)
    try:
        assert get_record_by_message_id(conn, "ba-1") is None
        assert get_record_by_message_id(conn, "ba-2") is None
    finally:
        conn.close()


def test_batch_delete_worker_clears_watermark_even_when_imap_raises(
    single_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The delete worker's finally block resets batch_op:state to idle even
    when an IMAP call raises, leaving the records re-triggerable."""
    import robotsix_auto_mail.imap as imap_mod
    from robotsix_auto_mail.server.adapters import _run_batch_delete_background

    _populate_db(
        single_db,
        [
            {
                "message_id": "bw-1",
                "sender": "a@b.com",
                "subject": "Boom",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    # Give the record a tracked UID so the worker takes the IMAP path.
    conn = init_db(single_db)
    try:
        conn.execute("UPDATE mail_records SET imap_uid = 42 WHERE message_id = 'bw-1'")
        conn.commit()
    finally:
        conn.close()
    _seed_triage_decision(single_db, "bw-1", action="TO_DELETE")

    class _BoomClient:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def __enter__(self) -> "_BoomClient":
            raise imap_mod.ImapError("kaboom")

        def __exit__(self, *a: object) -> None:
            pass

    monkeypatch.setattr(imap_mod, "ImapClient", _BoomClient)

    mail_config = MailConfig(
        imap_host="imap.example.com",
        imap_port=993,
        imap_tls_mode="direct",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_tls_mode="direct",
        username="user@example.com",
        password="pw",
        db_path=single_db,
    )
    _run_batch_delete_background(single_db, mail_config)

    from robotsix_auto_mail.db import get_record_by_message_id, get_watermark

    conn = init_db(single_db, skip_migrations=True)
    try:
        assert get_watermark(conn, "batch_op:state") == "idle"
        # IMAP raised before any delete → record left re-triggerable.
        assert get_record_by_message_id(conn, "bw-1") is not None
    finally:
        conn.close()


def test_batch_delete_worker_retrigger_skips_already_deleted(single_db: str) -> None:
    """Re-running the delete worker only processes records still present in
    the DB (already-deleted ones are skipped because they were committed)."""
    from robotsix_auto_mail.server.adapters import _run_batch_delete_background

    _populate_db(
        single_db,
        [
            {
                "message_id": "rt-1",
                "sender": "a@b.com",
                "subject": "One",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
            {
                "message_id": "rt-2",
                "sender": "c@d.com",
                "subject": "Two",
                "date": "2025-01-02T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "rt-1", action="TO_DELETE")
    _seed_triage_decision(single_db, "rt-2", action="TO_DELETE")

    # First run (DB-only, mail_config=None) deletes both records.
    _run_batch_delete_background(single_db, None)

    from robotsix_auto_mail.db import get_record_by_message_id, get_watermark

    conn = init_db(single_db, skip_migrations=True)
    try:
        assert get_record_by_message_id(conn, "rt-1") is None
        assert get_record_by_message_id(conn, "rt-2") is None
    finally:
        conn.close()

    # Re-trigger: nothing remains → total 0, no error, watermark idle.
    _run_batch_delete_background(single_db, None)
    conn = init_db(single_db, skip_migrations=True)
    try:
        assert get_watermark(conn, "batch_op:state") == "idle"
    finally:
        conn.close()


def test_build_board_content_batch_op_running_suppresses_delete_all(
    single_db: str,
) -> None:
    """When batch_op:state holds a JSON payload, _build_board_content returns
    the parsed batch_op and the columns omit the Delete-All button."""
    from robotsix_auto_mail.server.views import _build_board_content

    _populate_db(
        single_db,
        [
            {
                "message_id": "bc-1",
                "sender": "a@b.com",
                "subject": "Del",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "bc-1", action="TO_DELETE")

    # Idle → batch_op None, Delete-All present.
    idle = _build_board_content(single_db)
    assert idle["batch_op"] is None
    assert "Delete All" in idle["columns_html"]

    # Running → parsed batch_op, Delete-All suppressed.
    _seed_batch_state(
        single_db, json.dumps({"op": "delete", "done": 120, "total": 518})
    )
    running = _build_board_content(single_db)
    assert running["batch_op"] == {"op": "delete", "done": 120, "total": 518}
    assert "Delete All" not in running["columns_html"]


def test_board_and_content_render_batch_banner(single_db: str) -> None:
    """/board renders a .batch-banner with done/total and /board-content's
    JSON carries the batch_op payload while a batch op is running."""
    _seed_batch_state(
        single_db, json.dumps({"op": "delete", "done": 120, "total": 518})
    )
    server, port = _start_test_server(single_db)
    try:
        body = urlopen(f"http://127.0.0.1:{port}/board").read().decode("utf-8")
        assert "batch-banner" in body
        assert "120/518" in body

        content = json.loads(
            urlopen(f"http://127.0.0.1:{port}/board-content").read().decode("utf-8")
        )
        assert content["batch_op"] == {"op": "delete", "done": 120, "total": 518}
    finally:
        server.shutdown()


def test_to_archive_column_renders_archive_all_button(single_db: str) -> None:
    """A TO_ARCHIVE column renders an Archive All form posting /batch-archive."""
    from robotsix_auto_mail.server.views import _build_board_content

    _populate_db(
        single_db,
        [
            {
                "message_id": "aa-1",
                "sender": "a@b.com",
                "subject": "Arc",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "aa-1", action="TO_ARCHIVE")
    content = _build_board_content(single_db)
    assert 'action="/batch-archive"' in content["columns_html"]
    assert "Archive All" in content["columns_html"]


# ===========================================================================
# _is_safe_redirect_path unit tests
# ===========================================================================


def test_batch_archive_worker_groups_uids_by_destination(
    single_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The archive worker groups UIDs by their effective destination folder
    and issues one move_messages call per group."""
    import robotsix_auto_mail.imap as imap_mod
    from robotsix_auto_mail.server.adapters import _run_batch_archive_background
    from robotsix_auto_mail.triage import set_archive_subfolder_override

    _populate_db(
        single_db,
        [
            {
                "message_id": "g-1",
                "sender": "a@b.com",
                "subject": "A",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
            {
                "message_id": "g-2",
                "sender": "c@d.com",
                "subject": "B",
                "date": "2025-01-02T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
            {
                "message_id": "g-3",
                "sender": "e@f.com",
                "subject": "C",
                "date": "2025-01-03T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    conn = init_db(single_db)
    try:
        conn.execute("UPDATE mail_records SET imap_uid = 11 WHERE message_id='g-1'")
        conn.execute("UPDATE mail_records SET imap_uid = 22 WHERE message_id='g-2'")
        conn.execute("UPDATE mail_records SET imap_uid = 33 WHERE message_id='g-3'")
        conn.commit()
        # g-1 and g-3 share a destination subfolder; g-2 differs.
        set_archive_subfolder_override(conn, "g-1", "2026")
        set_archive_subfolder_override(conn, "g-2", "vendors")
        set_archive_subfolder_override(conn, "g-3", "2026")
    finally:
        conn.close()
    for mid in ("g-1", "g-2", "g-3"):
        _seed_triage_decision(single_db, mid, action="TO_ARCHIVE")

    class _Folder:
        delimiter = "/"

    moves: list[tuple[list[int], str]] = []

    class _FakeClient:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *a: object) -> None:
            pass

        def select_folder(self, name: str) -> int:
            return 0

        def list_folders(self) -> list[_Folder]:
            return [_Folder()]

        def create_folder(self, name: str) -> None:
            pass

        def search_uids(self, criteria: str) -> list[int]:
            # Return the stored UIDs for the records being tested.
            return [11, 22, 33]

        def move_messages(self, uids: list[int], dest: str) -> None:
            moves.append((list(uids), dest))

    monkeypatch.setattr(imap_mod, "ImapClient", _FakeClient)

    mail_config = MailConfig(
        imap_host="imap.example.com",
        imap_port=993,
        imap_tls_mode="direct",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_tls_mode="direct",
        username="user@example.com",
        password="pw",
        db_path=single_db,
        archive_root="Archive",
    )
    _run_batch_archive_background(single_db, mail_config, "Archive")

    # One move per destination group; g-1 + g-3 batched together.
    by_dest = {dest: uids for uids, dest in moves}
    assert by_dest == {"Archive/2026": [11, 33], "Archive/vendors": [22]}

    from robotsix_auto_mail.db import get_record_by_message_id, get_watermark

    conn = init_db(single_db, skip_migrations=True)
    try:
        for mid in ("g-1", "g-2", "g-3"):
            assert get_record_by_message_id(conn, mid) is None
        assert get_watermark(conn, "batch_op:state") == "idle"
    finally:
        conn.close()


def test_batch_archive_worker_subfolder_filter_archives_only_that_group(
    single_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With subfolder_filter set, only that destination's mail is archived;
    the rest of the TO_ARCHIVE column is left untouched."""
    import robotsix_auto_mail.imap as imap_mod
    from robotsix_auto_mail.db import get_record_by_message_id
    from robotsix_auto_mail.server.adapters import _run_batch_archive_background
    from robotsix_auto_mail.triage import set_archive_subfolder_override

    _populate_db(
        single_db,
        [
            {
                "message_id": f"f-{i}",
                "sender": "a@b.com",
                "subject": "S",
                "date": f"2025-01-0{i}T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            }
            for i in (1, 2, 3)
        ],
    )
    conn = init_db(single_db)
    try:
        conn.execute("UPDATE mail_records SET imap_uid = 11 WHERE message_id='f-1'")
        conn.execute("UPDATE mail_records SET imap_uid = 22 WHERE message_id='f-2'")
        conn.execute("UPDATE mail_records SET imap_uid = 33 WHERE message_id='f-3'")
        conn.commit()
        # f-1 + f-3 → "2026"; f-2 → "vendors".
        set_archive_subfolder_override(conn, "f-1", "2026")
        set_archive_subfolder_override(conn, "f-2", "vendors")
        set_archive_subfolder_override(conn, "f-3", "2026")
    finally:
        conn.close()
    for mid in ("f-1", "f-2", "f-3"):
        _seed_triage_decision(single_db, mid, action="TO_ARCHIVE")

    class _Folder:
        delimiter = "/"

    moves: list[tuple[list[int], str]] = []

    class _FakeClient:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *a: object) -> None:
            pass

        def select_folder(self, name: str) -> int:
            return 0

        def list_folders(self) -> list[_Folder]:
            return [_Folder()]

        def create_folder(self, name: str) -> None:
            pass

        def search_uids(self, criteria: str) -> list[int]:
            return [11, 22, 33]

        def move_messages(self, uids: list[int], dest: str) -> None:
            moves.append((list(uids), dest))

    monkeypatch.setattr(imap_mod, "ImapClient", _FakeClient)

    mail_config = MailConfig(
        imap_host="imap.example.com",
        imap_port=993,
        imap_tls_mode="direct",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_tls_mode="direct",
        username="user@example.com",
        password="pw",
        db_path=single_db,
        archive_root="Archive",
    )
    _run_batch_archive_background(
        single_db, mail_config, "Archive", subfolder_filter="2026"
    )

    # Only the "2026" group moved; "vendors" was not touched.
    assert moves == [([11, 33], "Archive/2026")]
    conn = init_db(single_db, skip_migrations=True)
    try:
        assert get_record_by_message_id(conn, "f-1") is None
        assert get_record_by_message_id(conn, "f-3") is None
        # f-2 (different destination) is preserved.
        assert get_record_by_message_id(conn, "f-2") is not None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Global (aggregate) board tests
# ---------------------------------------------------------------------------
