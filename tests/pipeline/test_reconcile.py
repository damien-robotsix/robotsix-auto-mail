"""Tests for reconcile_records."""

from __future__ import annotations

import sqlite3
from unittest import mock

from tests.pipeline._helpers import _mock_imap_client

from robotsix_auto_mail.db import (
    MailRecord,
    get_record_by_message_id,
    insert_record,
)
from robotsix_auto_mail.imap import ImapError
from robotsix_auto_mail.pipeline import reconcile_records

# reconcile_records tests
# ---------------------------------------------------------------------------


def test_reconcile_empty_db(
    conn: sqlite3.Connection,
) -> None:
    """No tracked records → returns (0, 0), no IMAP calls made."""
    imap = _mock_imap_client()

    healed, removed = reconcile_records(conn, imap)

    assert healed == 0
    assert removed == 0
    imap.select_folder.assert_not_called()
    imap.search_uids.assert_not_called()


def test_reconcile_prunes_user_moved_mail(
    conn: sqlite3.Connection,
) -> None:
    """UID not in source folder; cross_folder_resolve finds it in a different
    folder → record is REMOVED (user moved it manually, not auto-mail)."""

    # Seed a record.
    insert_record(
        conn,
        MailRecord(
            message_id="<moved@x>",
            sender="x@x.com",
            subject="Moved",
            date="2025-01-01T00:00:00",
            imap_uid=42,
            source_folder="INBOX",
        ),
    )

    imap = _mock_imap_client()
    # UID 42 is NOT in INBOX.
    imap.search_uids.return_value = []

    with mock.patch(
        "robotsix_auto_mail.imap.cross_folder_resolve",
        return_value=("Projects", 77),
    ) as mock_resolve:
        healed, removed = reconcile_records(conn, imap)

    # Different folder → prune, not heal.
    assert healed == 0
    assert removed == 1

    # Record should be gone.
    assert get_record_by_message_id(conn, "<moved@x>") is None

    # Verify cross_folder_resolve was called with source_folder.
    mock_resolve.assert_called_once_with(imap, "<moved@x>", source_folder="INBOX")


def test_reconcile_heals_uid_reassignment(
    conn: sqlite3.Connection,
) -> None:
    """UID not in source folder; cross_folder_resolve finds it in the SAME
    folder with a new UID (UIDVALIDITY change) → record is HEALED."""

    insert_record(
        conn,
        MailRecord(
            message_id="<renumbered@x>",
            sender="x@x.com",
            subject="Renumbered",
            date="2025-01-01T00:00:00",
            imap_uid=42,
            source_folder="INBOX",
        ),
    )

    imap = _mock_imap_client()
    # UID 42 is NOT in INBOX (old UID gone).
    imap.search_uids.return_value = []

    # cross_folder_resolve with source_folder="INBOX" finds it still in INBOX
    # but under a new UID (99).
    with mock.patch(
        "robotsix_auto_mail.imap.cross_folder_resolve",
        return_value=("INBOX", 99),
    ) as mock_resolve:
        healed, removed = reconcile_records(conn, imap)

    # Same folder, new UID → heal.
    assert healed == 1
    assert removed == 0

    # Record should be updated with the new UID, same source_folder.
    updated = get_record_by_message_id(conn, "<renumbered@x>")
    assert updated is not None
    assert updated.source_folder == "INBOX"
    assert updated.imap_uid == 99

    mock_resolve.assert_called_once_with(imap, "<renumbered@x>", source_folder="INBOX")


def test_reconcile_removes_deleted_mail(
    conn: sqlite3.Connection,
) -> None:
    """UID not found; cross_folder_resolve returns None → record removed."""

    insert_record(
        conn,
        MailRecord(
            message_id="<deleted@x>",
            sender="x@x.com",
            subject="Deleted",
            date="2025-01-01T00:00:00",
            imap_uid=99,
            source_folder="INBOX",
        ),
    )

    imap = _mock_imap_client()
    imap.search_uids.return_value = []  # UID 99 not found.

    with mock.patch(
        "robotsix_auto_mail.imap.cross_folder_resolve",
        return_value=None,
    ):
        healed, removed = reconcile_records(conn, imap)

    assert healed == 0
    assert removed == 1

    # Record should be gone.
    assert get_record_by_message_id(conn, "<deleted@x>") is None


def test_reconcile_mixed_scenario(
    conn: sqlite3.Connection,
) -> None:
    """Three records: one fine, one same-folder UID reassignment (healed),
    one different-folder user move (removed)."""

    for mid, uid in [
        ("<fine@x>", 1),
        ("<renumbered@x>", 2),
        ("<moved@x>", 3),
    ]:
        insert_record(
            conn,
            MailRecord(
                message_id=mid,
                sender="x@x.com",
                subject="Test",
                date="2025-01-01T00:00:00",
                imap_uid=uid,
                source_folder="INBOX",
            ),
        )

    imap = _mock_imap_client()
    # search_uids returns only UID 1 — UIDs 2 and 3 are missing.
    imap.search_uids.return_value = [1]

    def _fake_resolve(client, message_id, source_folder=None):
        if message_id == "<renumbered@x>":
            # Same folder, new UID → should be healed.
            return ("INBOX", 99)
        if message_id == "<moved@x>":
            # Different folder → should be removed.
            return ("Projects", 77)
        raise AssertionError(f"unexpected resolve call for {message_id}")

    with mock.patch(
        "robotsix_auto_mail.imap.cross_folder_resolve",
        side_effect=_fake_resolve,
    ):
        healed, removed = reconcile_records(conn, imap)

    assert healed == 1
    assert removed == 1

    # Fine record untouched.
    fine = get_record_by_message_id(conn, "<fine@x>")
    assert fine is not None
    assert fine.source_folder == "INBOX"
    assert fine.imap_uid == 1

    # Renumbered record healed (same folder, new UID).
    renumbered = get_record_by_message_id(conn, "<renumbered@x>")
    assert renumbered is not None
    assert renumbered.source_folder == "INBOX"
    assert renumbered.imap_uid == 99

    # Moved record gone (different folder).
    assert get_record_by_message_id(conn, "<moved@x>") is None


def test_reconcile_transient_error_preserves_records(
    conn: sqlite3.Connection,
) -> None:
    """search_uids raises ImapError → (0, 0), all records preserved."""

    insert_record(
        conn,
        MailRecord(
            message_id="<keep@x>",
            sender="x@x.com",
            subject="Keep",
            date="2025-01-01T00:00:00",
            imap_uid=42,
            source_folder="INBOX",
        ),
    )

    imap = _mock_imap_client()
    imap.search_uids.side_effect = ImapError("connection lost")

    healed, removed = reconcile_records(conn, imap)

    assert healed == 0
    assert removed == 0

    # Record still exists.
    assert get_record_by_message_id(conn, "<keep@x>") is not None


def test_reconcile_chunks_uid_search(
    conn: sqlite3.Connection,
) -> None:
    """600 UIDs in one folder → two search_uids calls (500 + 100)."""

    # Insert 600 records, UIDs 1..600.
    for uid in range(1, 601):
        insert_record(
            conn,
            MailRecord(
                message_id=f"<msg{uid}@x>",
                sender="x@x.com",
                subject="Chunk",
                date="2025-01-01T00:00:00",
                imap_uid=uid,
                source_folder="INBOX",
            ),
        )

    imap = _mock_imap_client()
    # Return all UIDs as found (no healing needed).
    imap.search_uids.return_value = list(range(1, 601))

    with mock.patch(
        "robotsix_auto_mail.imap.cross_folder_resolve",
    ) as mock_resolve:
        healed, removed = reconcile_records(conn, imap)

    assert healed == 0
    assert removed == 0

    # Two search_uids calls expected.
    assert imap.search_uids.call_count == 2

    # First call: 500 UIDs.
    call1_args = imap.search_uids.call_args_list[0][0]
    assert "UID " in call1_args[0]
    # Second call: 100 UIDs.
    call2_args = imap.search_uids.call_args_list[1][0]
    assert "UID " in call2_args[0]

    # cross_folder_resolve never called (all UIDs found).
    mock_resolve.assert_not_called()


def test_reconcile_prunes_record_drifted_from_monitored_folder(
    conn: sqlite3.Connection,
) -> None:
    """A record whose source_folder != monitored_folder is pruned outright,
    without any IMAP round-trip.

    This is the self-heal path for records stranded by the pre-#502 reconcile,
    which rewrote source_folder to wherever a user-moved mail landed (e.g. an
    archive sub-folder) instead of pruning the record.
    """

    # A live INBOX record (should survive) ...
    insert_record(
        conn,
        MailRecord(
            message_id="<inbox@x>",
            sender="x@x.com",
            subject="Still in inbox",
            date="2025-01-01T00:00:00",
            imap_uid=10,
            source_folder="INBOX",
        ),
    )
    # ... and a record stranded in an archive sub-folder.
    insert_record(
        conn,
        MailRecord(
            message_id="<archived@x>",
            sender="x@x.com",
            subject="User-archived",
            date="2025-01-01T00:00:00",
            imap_uid=360,
            source_folder="INBOX.archive.Projects",
        ),
    )

    imap = _mock_imap_client()
    # INBOX UID 10 is still present.
    imap.search_uids.return_value = [10]

    with mock.patch(
        "robotsix_auto_mail.imap.cross_folder_resolve",
    ) as mock_resolve:
        healed, removed = reconcile_records(conn, imap, monitored_folder="INBOX")

    assert healed == 0
    assert removed == 1

    # The drifted record is gone; the INBOX record survives.
    assert get_record_by_message_id(conn, "<archived@x>") is None
    assert get_record_by_message_id(conn, "<inbox@x>") is not None

    # The drifted folder is pruned without selecting it or resolving it.
    imap.select_folder.assert_called_once_with("INBOX")
    mock_resolve.assert_not_called()
