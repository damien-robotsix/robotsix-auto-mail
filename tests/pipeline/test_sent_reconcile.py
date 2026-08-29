"""Tests for reconcile_sent_drafts (auto-archive-on-send)."""

from __future__ import annotations

import sqlite3

from robotsix_auto_mail.db import (
    MailRecord,
    insert_record,
    list_unreconciled_compose_links,
    record_compose_link,
)
from robotsix_auto_mail.imap import MailboxInfo
from robotsix_auto_mail.pipeline import reconcile_sent_drafts
from robotsix_auto_mail.triage import (
    TO_ARCHIVE,
    get_triage_decision,
    set_triage_decision,
)
from tests.pipeline._helpers import _mock_imap_client

_SENT_FOLDER = MailboxInfo(name="Sent", attributes=("\\Sent",), delimiter="/")


def _seed_card(conn: sqlite3.Connection, message_id: str) -> None:
    insert_record(
        conn,
        MailRecord(
            message_id=message_id,
            sender="alice@example.com",
            subject="Question",
            date="2025-01-01T00:00:00",
            imap_uid=1,
            source_folder="INBOX",
        ),
    )


def test_no_links_is_noop(conn: sqlite3.Connection) -> None:
    """No pending links → (0, 0) and no IMAP calls."""
    imap = _mock_imap_client()

    assert reconcile_sent_drafts(conn, imap) == (0, 0)
    imap.list_folders.assert_not_called()
    imap.search_uids.assert_not_called()


def test_archives_card_when_reply_in_sent(conn: sqlite3.Connection) -> None:
    """A reply found in Sent archives the card and reconciles the link."""
    _seed_card(conn, "<orig@x>")
    record_compose_link(conn, "<orig@x>", subject="Re: Question")

    imap = _mock_imap_client()
    imap.list_folders.return_value = [_SENT_FOLDER]
    imap.search_uids.return_value = [7]

    archived, checked = reconcile_sent_drafts(conn, imap)

    assert (archived, checked) == (1, 1)
    decision = get_triage_decision(conn, "<orig@x>")
    assert decision is not None
    assert decision.action == TO_ARCHIVE
    assert decision.source == "agent"
    # Link is now reconciled.
    assert list_unreconciled_compose_links(conn) == []


def test_no_false_archive_when_reply_absent(conn: sqlite3.Connection) -> None:
    """Reply not in Sent → card untouched and link stays pending."""
    _seed_card(conn, "<orig@x>")
    record_compose_link(conn, "<orig@x>")

    imap = _mock_imap_client()
    imap.list_folders.return_value = [_SENT_FOLDER]
    imap.search_uids.return_value = []

    archived, checked = reconcile_sent_drafts(conn, imap)

    assert (archived, checked) == (0, 1)
    assert get_triage_decision(conn, "<orig@x>") is None
    assert len(list_unreconciled_compose_links(conn)) == 1


def test_idempotent_second_run(conn: sqlite3.Connection) -> None:
    """A second run finds no unreconciled link and archives nothing more."""
    _seed_card(conn, "<orig@x>")
    record_compose_link(conn, "<orig@x>")

    imap = _mock_imap_client()
    imap.list_folders.return_value = [_SENT_FOLDER]
    imap.search_uids.return_value = [7]

    assert reconcile_sent_drafts(conn, imap) == (1, 1)
    # Nothing left to reconcile.
    assert reconcile_sent_drafts(conn, imap) == (0, 0)


def test_missing_card_still_reconciles_link(conn: sqlite3.Connection) -> None:
    """When the card is already gone the link is stamped, archived stays 0."""
    record_compose_link(conn, "<gone@x>")  # no matching mail_records row

    imap = _mock_imap_client()
    imap.list_folders.return_value = [_SENT_FOLDER]
    imap.search_uids.return_value = [7]

    archived, checked = reconcile_sent_drafts(conn, imap)

    assert (archived, checked) == (0, 1)
    assert list_unreconciled_compose_links(conn) == []


def test_user_decision_is_not_overwritten(conn: sqlite3.Connection) -> None:
    """An operator's own triage decision survives auto-archive."""
    _seed_card(conn, "<orig@x>")
    set_triage_decision(conn, "<orig@x>", "TO_ANSWER", source="user")
    record_compose_link(conn, "<orig@x>")

    imap = _mock_imap_client()
    imap.list_folders.return_value = [_SENT_FOLDER]
    imap.search_uids.return_value = [7]

    reconcile_sent_drafts(conn, imap)

    decision = get_triage_decision(conn, "<orig@x>")
    assert decision is not None
    assert decision.action == "TO_ANSWER"
    assert decision.source == "user"
    # Link is still stamped reconciled so it is not retried forever.
    assert list_unreconciled_compose_links(conn) == []


def test_no_sent_folder(conn: sqlite3.Connection) -> None:
    """No Sent folder → nothing archived and the link stays pending."""
    _seed_card(conn, "<orig@x>")
    record_compose_link(conn, "<orig@x>")

    imap = _mock_imap_client()
    imap.list_folders.return_value = [
        MailboxInfo(name="INBOX", attributes=(), delimiter="/")
    ]

    archived, checked = reconcile_sent_drafts(conn, imap)

    assert (archived, checked) == (0, 1)
    assert len(list_unreconciled_compose_links(conn)) == 1
