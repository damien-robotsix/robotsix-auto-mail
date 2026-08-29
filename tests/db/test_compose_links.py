"""Tests for the ``compose_links`` CRUD helpers."""

from __future__ import annotations

import sqlite3

from robotsix_auto_mail.db import (
    list_unreconciled_compose_links,
    mark_compose_link_reconciled,
    record_compose_link,
)


def test_record_and_list_unreconciled(conn: sqlite3.Connection) -> None:
    """A recorded link is returned as unreconciled with its fields."""
    record_compose_link(
        conn, "<orig@x>", subject="Re: Hello", to_addr="bob@example.com"
    )

    links = list_unreconciled_compose_links(conn)

    assert len(links) == 1
    link = links[0]
    assert link.reply_to_message_id == "<orig@x>"
    assert link.draft_subject == "Re: Hello"
    assert link.draft_to == "bob@example.com"
    assert link.created_at != ""
    assert link.reconciled_at is None


def test_record_is_idempotent_upsert(conn: sqlite3.Connection) -> None:
    """Recording the same reply target twice keeps a single row."""
    record_compose_link(conn, "<orig@x>", subject="v1")
    record_compose_link(conn, "<orig@x>", subject="v2")

    links = list_unreconciled_compose_links(conn)

    assert len(links) == 1
    assert links[0].draft_subject == "v2"


def test_mark_reconciled_removes_from_unreconciled(
    conn: sqlite3.Connection,
) -> None:
    """A reconciled link no longer appears in the unreconciled list."""
    record_compose_link(conn, "<orig@x>")

    mark_compose_link_reconciled(conn, "<orig@x>")

    assert list_unreconciled_compose_links(conn) == []


def test_recompose_reopens_reconciled_link(conn: sqlite3.Connection) -> None:
    """Re-composing after reconcile clears ``reconciled_at`` again."""
    record_compose_link(conn, "<orig@x>")
    mark_compose_link_reconciled(conn, "<orig@x>")
    assert list_unreconciled_compose_links(conn) == []

    record_compose_link(conn, "<orig@x>", subject="second draft")

    links = list_unreconciled_compose_links(conn)
    assert len(links) == 1
    assert links[0].draft_subject == "second draft"


def test_mark_reconciled_is_idempotent(conn: sqlite3.Connection) -> None:
    """Marking an already-reconciled (or unknown) link is a safe no-op."""
    mark_compose_link_reconciled(conn, "<unknown@x>")  # no row — no error

    record_compose_link(conn, "<orig@x>")
    mark_compose_link_reconciled(conn, "<orig@x>")
    mark_compose_link_reconciled(conn, "<orig@x>")

    assert list_unreconciled_compose_links(conn) == []
