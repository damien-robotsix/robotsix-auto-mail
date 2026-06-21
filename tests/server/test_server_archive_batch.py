"""Tests for batch archive/delete worker concurrency, watermark, and global board."""

from __future__ import annotations

import json
from urllib.request import urlopen

import pytest
from tests.server.conftest import (
    _populate_db,
    _post_to_path,
    _seed_batch_state,
    _seed_triage_decision,
    _start_test_server,
    _wait_for_batch_idle,
)

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import init_db


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
