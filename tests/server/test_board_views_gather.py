"""Unit tests for ``_gather_account_board_data`` from
``robotsix_auto_mail.server.views.board_data``.

Uses a real temp SQLite DB (``single_db`` fixture from
``tests/server/conftest.py``).
"""

from __future__ import annotations

import json
from unittest import mock

from robotsix_auto_mail.config import DEFAULT_ARCHIVE_ROOT
from robotsix_auto_mail.db import init_db, set_watermark
from tests.server.conftest_helpers import (
    _populate_db,
    _seed_archive_override,
    _seed_archive_structure,
    _seed_triage_decision,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_gather_db(db_path: str) -> None:
    """Populate *db_path* with records used by _gather_account_board_data tests."""
    _populate_db(
        db_path,
        [
            {
                "message_id": "mid-inbox",
                "sender": "a@x.com",
                "subject": "Inbox mail",
                "date": "2025-01-01T00:00:00",
                "body_plain": "Body A",
                "status": "to_read",
            },
            {
                "message_id": "mid-delete",
                "sender": "b@x.com",
                "subject": "Delete mail",
                "date": "2025-01-02T00:00:00",
                "body_plain": "Body B",
                "status": "to_read",
            },
            {
                "message_id": "mid-archive-1",
                "sender": "c@x.com",
                "subject": "Archive 1",
                "date": "2025-01-03T00:00:00",
                "body_plain": "Body C",
                "status": "to_read",
            },
            {
                "message_id": "mid-archive-2",
                "sender": "d@x.com",
                "subject": "Archive 2",
                "date": "2025-01-04T00:00:00",
                "body_plain": "Body D",
                "status": "to_read",
            },
            {
                "message_id": "mid-unknown-action",
                "sender": "e@x.com",
                "subject": "Unknown",
                "date": "2025-01-05T00:00:00",
                "body_plain": "Body E",
                "status": "to_read",
            },
            {
                "message_id": "mid-notes",
                "sender": "f@x.com",
                "subject": "Notes",
                "date": "2025-01-06T00:00:00",
                "body_plain": "Body F",
                "status": "to_read",
            },
        ],
    )
    # Add a notes value via raw SQL (the _populate_db helper doesn't cover it).
    conn = init_db(db_path)
    try:
        conn.execute(
            "UPDATE mail_records SET notes = ? WHERE message_id = ?",
            ("some-note", "mid-notes"),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# _gather_account_board_data
# ---------------------------------------------------------------------------


def test_gather_batch_op_idle(single_db: str) -> None:
    """batch_op is None when the watermark is unset."""
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    result = _gather_account_board_data(single_db)
    assert result["batch_op"] is None


def test_gather_batch_op_idle_explicit(single_db: str) -> None:
    """batch_op is None when the watermark is 'idle'."""
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    conn = init_db(single_db)
    try:
        set_watermark(conn, "batch_op:state", "idle")
    finally:
        conn.close()

    result = _gather_account_board_data(single_db)
    assert result["batch_op"] is None


def test_gather_batch_op_json_payload(single_db: str) -> None:
    """batch_op parses a valid JSON progress payload."""
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    conn = init_db(single_db)
    try:
        set_watermark(
            conn,
            "batch_op:state",
            json.dumps({"op": "delete", "done": 3, "total": 10}),
        )
    finally:
        conn.close()

    result = _gather_account_board_data(single_db)
    assert result["batch_op"] == {"op": "delete", "done": 3, "total": 10}


def test_gather_batch_op_running_sentinel(single_db: str) -> None:
    """batch_op is the bare sentinel dict when watermark is just 'running'."""
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    conn = init_db(single_db)
    try:
        set_watermark(conn, "batch_op:state", "running")
    finally:
        conn.close()

    result = _gather_account_board_data(single_db)
    assert result["batch_op"] == {"op": None, "done": None, "total": None}


def test_gather_column_bucketing_no_decision(single_db: str) -> None:
    """A record with no triage decision lands in INBOX."""
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    result = _gather_account_board_data(single_db)
    inbox_mids = {r.message_id for r in result["column_buckets"]["INBOX"]}
    assert "mid-inbox" in inbox_mids


def test_gather_column_bucketing_known_action(single_db: str) -> None:
    """A record whose decision action is a known board column lands there."""
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    _seed_triage_decision(single_db, "mid-delete", action="TO_DELETE")
    _seed_triage_decision(single_db, "mid-archive-1", action="TO_ARCHIVE")

    result = _gather_account_board_data(single_db)
    delete_mids = {r.message_id for r in result["column_buckets"]["TO_DELETE"]}
    archive_mids = {r.message_id for r in result["column_buckets"]["TO_ARCHIVE"]}
    assert "mid-delete" in delete_mids
    assert "mid-archive-1" in archive_mids


def test_gather_column_bucketing_unknown_action_fallback(single_db: str) -> None:
    """A record whose decision action is NOT in _BOARD_COLUMNS falls back to
    HUMAN_TRIAGE.

    The triage_decisions table has a CHECK constraint that restricts
    actions to the known set, so we monkeypatch ``list_triage_decisions``
    to return a mock decision with an unrecognised action.
    """
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    # Build a mock TriageDecision with an action NOT in _BOARD_COLUMNS.
    fake_decision = mock.MagicMock()
    fake_decision.message_id = "mid-unknown-action"
    fake_decision.action = "NONEXISTENT_COLUMN"

    with mock.patch(
        "robotsix_auto_mail.server.views.board_data.list_triage_decisions",
        return_value=[fake_decision],
    ):
        result = _gather_account_board_data(single_db)

    ht_mids = {r.message_id for r in result["column_buckets"]["HUMAN_TRIAGE"]}
    assert "mid-unknown-action" in ht_mids


def test_gather_to_archive_sort_by_destination(single_db: str) -> None:
    """TO_ARCHIVE records are stable-sorted by archive subfolder."""
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    _seed_triage_decision(single_db, "mid-archive-1", action="TO_ARCHIVE")
    _seed_triage_decision(single_db, "mid-archive-2", action="TO_ARCHIVE")

    # Give mid-archive-1 a subfolder override that sorts after mid-archive-2's.
    # mid-archive-2 → "a_sub" sorts before mid-archive-1 → "z_sub".
    from robotsix_auto_mail.db import init_db as _init_db
    from robotsix_auto_mail.triage import _save_archive_overrides

    conn = _init_db(single_db)
    try:
        _save_archive_overrides(
            conn, {"mid-archive-1": "z_sub", "mid-archive-2": "a_sub"}
        )
    finally:
        conn.close()

    result = _gather_account_board_data(single_db)
    to_archive = result["column_buckets"]["TO_ARCHIVE"]
    assert len(to_archive) == 2
    # mid-archive-2 (subfolder "a_sub") should come before mid-archive-1.
    assert to_archive[0].message_id == "mid-archive-2"
    assert to_archive[1].message_id == "mid-archive-1"


def test_gather_archive_subfolders_computed(single_db: str) -> None:
    """archive_subfolders map is populated for TO_ARCHIVE records."""
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    _seed_triage_decision(single_db, "mid-archive-1", action="TO_ARCHIVE")
    _seed_archive_override(single_db, "mid-archive-1", "taxes/2025")

    result = _gather_account_board_data(single_db)
    assert "mid-archive-1" in result["archive_subfolders"]
    # The override is normalized — should contain "taxes/2025" or "taxes-2025".
    assert result["archive_subfolders"]["mid-archive-1"] != ""


def test_gather_folder_exists(single_db: str) -> None:
    """folder_exists is True when the subfolder path exists in archive_structure."""
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    _seed_triage_decision(single_db, "mid-archive-1", action="TO_ARCHIVE")
    _seed_triage_decision(single_db, "mid-archive-2", action="TO_ARCHIVE")

    # Override archive-1 to a known folder, archive-2 to an unknown one.
    _seed_archive_override(single_db, "mid-archive-1", "known-folder")
    _seed_archive_override(single_db, "mid-archive-2", "unknown-folder")

    _seed_archive_structure(
        single_db,
        [DEFAULT_ARCHIVE_ROOT, f"{DEFAULT_ARCHIVE_ROOT}/known-folder"],
    )

    result = _gather_account_board_data(single_db)
    assert result["folder_exists"]["mid-archive-1"] is True
    assert result["folder_exists"]["mid-archive-2"] is False


def test_gather_unsubscribe_suggestions(single_db: str) -> None:
    """unsubscribe_suggestions is parsed from the watermark."""
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    suggestions = {
        "sender@example.com": {
            "method": "mailto",
            "url": "mailto:unsub@example.com",
            "description": "Reply to unsubscribe.",
        }
    }
    conn = init_db(single_db)
    try:
        set_watermark(conn, "unsubscribe_suggestions", json.dumps(suggestions))
    finally:
        conn.close()

    result = _gather_account_board_data(single_db)
    assert result["unsubscribe_suggestions"] == suggestions


def test_gather_unsubscribe_suggestions_malformed(single_db: str) -> None:
    """Malformed JSON in unsubscribe_suggestions yields an empty dict."""
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    conn = init_db(single_db)
    try:
        set_watermark(conn, "unsubscribe_suggestions", "not-json")
    finally:
        conn.close()

    result = _gather_account_board_data(single_db)
    assert result["unsubscribe_suggestions"] == {}


def test_gather_record_notes(single_db: str) -> None:
    """record_notes maps message_id to notes for records that have them."""
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    result = _gather_account_board_data(single_db)
    assert result["record_notes"] == {"mid-notes": "some-note"}
    # Records without notes are absent.
    assert "mid-inbox" not in result["record_notes"]


def test_gather_triage_running_false(single_db: str) -> None:
    """triage_running is False when watermark is unset."""
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    result = _gather_account_board_data(single_db)
    assert result["triage_running"] is False


def test_gather_triage_running_true(single_db: str) -> None:
    """triage_running is True when watermark is 'running'."""
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    conn = init_db(single_db)
    try:
        set_watermark(conn, "triage_run:state", "running")
    finally:
        conn.close()

    result = _gather_account_board_data(single_db)
    assert result["triage_running"] is True


def test_gather_returns_all_expected_keys(single_db: str) -> None:
    """The returned dict contains all expected keys."""
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    result = _gather_account_board_data(single_db)
    assert set(result.keys()) == {
        "triage_running",
        "batch_op",
        "health",
        "triage_by_mid",
        "column_buckets",
        "archive_subfolders",
        "archive_folders",
        "folder_exists",
        "unsubscribe_suggestions",
        "record_notes",
    }


def test_gather_archive_folders_stripped(single_db: str) -> None:
    """archive_folders strips the root prefix from each folder name."""
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    root = DEFAULT_ARCHIVE_ROOT
    _seed_archive_structure(
        single_db,
        [root, f"{root}/sub1", f"{root}/sub1/nested", f"{root}/sub2"],
    )
    result = _gather_account_board_data(single_db)
    assert "sub1" in result["archive_folders"]
    assert "sub1/nested" in result["archive_folders"]
    assert "sub2" in result["archive_folders"]
    # The root itself is excluded.
    assert "" not in result["archive_folders"]
    assert root not in result["archive_folders"]


def test_gather_unsubscribe_empty_when_unset(single_db: str) -> None:
    """unsubscribe_suggestions is empty dict when watermark is unset."""
    from robotsix_auto_mail.server.views.board_data import _gather_account_board_data

    _make_gather_db(single_db)
    result = _gather_account_board_data(single_db)
    assert result["unsubscribe_suggestions"] == {}
