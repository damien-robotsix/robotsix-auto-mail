"""Tests for archive proposal POST endpoint."""

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
