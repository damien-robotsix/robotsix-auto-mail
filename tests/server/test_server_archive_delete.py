"""Tests for archive and delete operations (IMAP interaction, batch ops) — delete flow."""

from __future__ import annotations

import json
from unittest import mock
from urllib.request import urlopen

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import init_db
from tests.server.conftest import (
    _populate_db,
    _post_form,
    _post_to_path,
    _seed_archive_override,
    _seed_archive_structure,
    _seed_triage_decision,
    _start_test_server,
    _start_test_server_with_mail_config,
    _wait_for_batch_idle,
)

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
            mock.patch(
                "robotsix_auto_mail.server._action_mixin.record_user_action"
            ) as mock_record,
        ):
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.search_uids.return_value = []
            mock_cross.return_value = None  # mail gone

            status, body = _post_form(
                port, {"message_id": "stale-arch"}, path="/archive"
            )

        assert status == 302, f"Expected 302, got {status}: {body}"
        # Mail is gone: the stale path deletes the row and returns before
        # reaching the rules-recording step, so no user action is recorded.
        mock_record.assert_not_called()
    finally:
        server.shutdown()

    from robotsix_auto_mail.db import get_record_by_message_id

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
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urlopen(f"http://127.0.0.1:{port}/archive-proposal/nonexistent")
        assert exc_info.value.code == 404
    finally:
        server.shutdown()
