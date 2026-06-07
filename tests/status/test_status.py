"""Tests for ``robotsix_auto_mail.status``."""

from __future__ import annotations

import sqlite3
import tempfile
from typing import Any

from tests.conftest import _make_record

from robotsix_auto_mail.db import init_db, insert_record
from robotsix_auto_mail.status import (
    STATUS_LABELS,
    STATUS_ORDER,
    VALID_STATUSES,
    get_status,
    list_by_status,
    set_status,
)

# ---------------------------------------------------------------------------
# VALID_STATUSES / STATUS_ORDER / STATUS_LABELS
# ---------------------------------------------------------------------------


def test_status_order_is_the_awaiting_action_columns() -> None:
    """STATUS_ORDER holds the five awaiting-action columns, in order."""
    assert STATUS_ORDER == (
        "needs_reply",
        "waiting",
        "to_read",
        "no_action",
        "done",
    )


def test_valid_statuses_contains_exactly_the_new_values() -> None:
    """The constant holds exactly the five awaiting-action statuses."""
    assert VALID_STATUSES == frozenset(
        {"needs_reply", "waiting", "to_read", "no_action", "done"}
    )


def test_status_labels_cover_every_status_with_display_text() -> None:
    """STATUS_LABELS maps each status key to its board header label."""
    assert STATUS_LABELS == {
        "needs_reply": "Needs reply",
        "waiting": "Waiting on them",
        "to_read": "To read",
        "no_action": "No action",
        "done": "Done",
    }
    assert tuple(STATUS_LABELS) == STATUS_ORDER


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


def test_get_status_returns_none_for_unknown_message_id() -> None:
    """get_status returns None when the message_id is not in the table."""
    conn = init_db(":memory:")
    try:
        result = get_status(conn, "<nonexistent@example.com>")
        assert result is None
    finally:
        conn.close()


def test_get_status_returns_correct_status_for_known_record() -> None:
    """get_status returns the stored status string for a known message_id."""
    conn = init_db(":memory:")
    try:
        insert_record(conn, _make_record(message_id="<a@x.com>", status="to_read"))
        insert_record(
            conn, _make_record(message_id="<b@x.com>", status="needs_reply")
        )
        assert get_status(conn, "<a@x.com>") == "to_read"
        assert get_status(conn, "<b@x.com>") == "needs_reply"
    finally:
        conn.close()


class _CommitTracker:
    """Wraps a real ``sqlite3.Connection`` and tracks whether ``commit()``
    was called.  Needed because ``sqlite3.Connection.commit`` is a
    C-level method that cannot be monkey-patched with ``patch.object``.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.commit_called = False

    def commit(self) -> None:
        self.commit_called = True
        self._conn.commit()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def test_get_status_does_not_call_commit() -> None:
    """get_status is read-only and must not call conn.commit()."""
    conn = init_db(":memory:")
    try:
        insert_record(conn, _make_record(message_id="<x@x.com>", status="to_read"))
        tracker = _CommitTracker(conn)
        result = get_status(tracker, "<x@x.com>")  # type: ignore[arg-type]
        assert result == "to_read"
        assert tracker.commit_called is False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# set_status
# ---------------------------------------------------------------------------


def test_set_status_updates_and_returns_true() -> None:
    """set_status updates the status and returns True for an existing record."""
    conn = init_db(":memory:")
    try:
        insert_record(conn, _make_record(message_id="<a@x.com>", status="to_read"))
        assert get_status(conn, "<a@x.com>") == "to_read"
        result = set_status(conn, "<a@x.com>", "done")
        assert result is True
        assert get_status(conn, "<a@x.com>") == "done"
    finally:
        conn.close()


def test_set_status_returns_false_for_unknown_message_id() -> None:
    """set_status returns False when the message_id does not exist."""
    conn = init_db(":memory:")
    try:
        result = set_status(conn, "<no-such-id@x.com>", "no_action")
        assert result is False
    finally:
        conn.close()


def test_set_status_raises_valueerror_for_invalid_status() -> None:
    """set_status raises ValueError when new_status is not in VALID_STATUSES."""
    conn = init_db(":memory:")
    try:
        insert_record(conn, _make_record(message_id="<a@x.com>"))
        for bad in ("bogus", "", "inbox", "triaging", "archive", "  to_read  "):
            try:
                set_status(conn, "<a@x.com>", bad)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"set_status with {bad!r} did not raise ValueError"
                )
    finally:
        conn.close()


def test_set_status_calls_commit() -> None:
    """set_status calls conn.commit() so changes are visible to other connections."""
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        conn1 = init_db(f.name)
        try:
            insert_record(
                conn1, _make_record(message_id="<a@x.com>", status="to_read")
            )
            set_status(conn1, "<a@x.com>", "needs_reply")

            # Open a second connection and verify the update is visible.
            conn2 = sqlite3.connect(f.name)
            try:
                cur = conn2.execute(
                    "SELECT status FROM mail_records WHERE message_id = ?",
                    ("<a@x.com>",),
                )
                assert cur.fetchone()[0] == "needs_reply"
            finally:
                conn2.close()
        finally:
            conn1.close()


# ---------------------------------------------------------------------------
# list_by_status
# ---------------------------------------------------------------------------


def test_list_by_status_returns_only_matching_records() -> None:
    """list_by_status filters records by the given status."""
    conn = init_db(":memory:")
    try:
        insert_record(conn, _make_record(message_id="<a@x.com>", status="to_read"))
        insert_record(conn, _make_record(message_id="<b@x.com>", status="to_read"))
        insert_record(
            conn, _make_record(message_id="<c@x.com>", status="needs_reply")
        )

        to_read_records = list_by_status(conn, "to_read")
        needs_reply_records = list_by_status(conn, "needs_reply")
        done_records = list_by_status(conn, "done")

        assert len(to_read_records) == 2
        assert all(r.status == "to_read" for r in to_read_records)
        assert len(needs_reply_records) == 1
        assert needs_reply_records[0].message_id == "<c@x.com>"
        assert done_records == []
    finally:
        conn.close()


def test_list_by_status_returns_records_ordered_by_id_asc() -> None:
    """list_by_status returns records in id-ascending order."""
    conn = init_db(":memory:")
    try:
        insert_record(conn, _make_record(message_id="<b@x.com>", status="to_read"))
        insert_record(conn, _make_record(message_id="<a@x.com>", status="to_read"))
        insert_record(conn, _make_record(message_id="<c@x.com>", status="to_read"))

        records = list_by_status(conn, "to_read")
        ids = [r.id for r in records]
        assert ids == sorted(ids)
    finally:
        conn.close()


def test_list_by_status_raises_valueerror_for_invalid_status() -> None:
    """list_by_status raises ValueError for a status not in VALID_STATUSES."""
    conn = init_db(":memory:")
    try:
        for bad in ("bogus", "", "inbox", "triaging", "archive", "  to_read  "):
            try:
                list_by_status(conn, bad)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"list_by_status with {bad!r} did not raise ValueError"
                )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Argument shape
# ---------------------------------------------------------------------------


def test_all_functions_accept_sqlite3_connection_as_first_argument() -> None:
    """get_status, set_status, and list_by_status all accept a Connection."""
    conn = init_db(":memory:")
    try:
        insert_record(conn, _make_record(message_id="<a@x.com>", status="to_read"))

        # Merely calling each function confirms they accept a Connection.
        assert isinstance(get_status(conn, "<a@x.com>"), str)
        assert set_status(conn, "<a@x.com>", "done") is True
        assert isinstance(list_by_status(conn, "done"), list)
    finally:
        conn.close()
