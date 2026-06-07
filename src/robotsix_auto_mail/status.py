"""Status read/write operations for the ``mail_records`` table.

Provides transport-agnostic primitives that both the CLI ``board``
command and the upcoming web server can use uniformly.
"""

from __future__ import annotations

import sqlite3

from robotsix_auto_mail.db import MailRecord, row_to_mailrecord

#: Canonical status order for the kanban board, organised by the action the
#: user owes each mail (needs_reply → waiting → to_read → no_action → done).
STATUS_ORDER: tuple[str, ...] = (
    "needs_reply",
    "waiting",
    "to_read",
    "no_action",
    "done",
)
#: The canonical status values, matching the kanban columns.
VALID_STATUSES: frozenset[str] = frozenset(STATUS_ORDER)
#: Human-readable column labels (status key → board header text), in the same
#: order as :data:`STATUS_ORDER`.
STATUS_LABELS: dict[str, str] = {
    "needs_reply": "Needs reply",
    "waiting": "Waiting on them",
    "to_read": "To read",
    "no_action": "No action",
    "done": "Done",
}


def get_status(conn: sqlite3.Connection, message_id: str) -> str | None:
    """Return the status string for *message_id*, or ``None`` if not found.

    This is a read-only operation — it does **not** call
    ``conn.commit()``.
    """
    cur = conn.execute(
        "SELECT status FROM mail_records WHERE message_id = ?", (message_id,)
    )
    row = cur.fetchone()
    if row is None:
        return None
    value = row[0]
    if not isinstance(value, str):
        raise TypeError(f"Expected str from DB, got {type(value).__name__}")
    return value


def set_status(
    conn: sqlite3.Connection, message_id: str, new_status: str
) -> bool:
    """Update the status of *message_id* to *new_status*.

    Returns ``True`` if a row was updated, ``False`` if the
    *message_id* was not found.  Raises ``ValueError`` when
    *new_status* is not one of :data:`VALID_STATUSES`.

    This is a write operation — it calls ``conn.commit()``.
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(
            f"Invalid status: {new_status!r}. "
            f"Must be one of {sorted(VALID_STATUSES)}"
        )
    cur = conn.execute(
        "UPDATE mail_records SET status = ? WHERE message_id = ?",
        (new_status, message_id),
    )
    conn.commit()
    return cur.rowcount > 0


def list_by_status(
    conn: sqlite3.Connection, status: str,
) -> list[MailRecord]:
    """Return every ``MailRecord`` whose ``status`` column matches *status*.

    Rows are ordered by ``id ASC`` (consistent with
    :func:`~robotsix_auto_mail.db.list_records`).  Returns an empty
    list when no records match.

    Raises ``ValueError`` when *status* is not one of
    :data:`VALID_STATUSES`.

    This is a read-only operation — it does **not** call
    ``conn.commit()``.
    """
    if status not in VALID_STATUSES:
        raise ValueError(
            f"Invalid status: {status!r}. "
            f"Must be one of {sorted(VALID_STATUSES)}"
        )
    cur = conn.execute(
        "SELECT * FROM mail_records WHERE status = ? ORDER BY id ASC", (status,)
    )
    rows = cur.fetchall()
    col_names = [desc[0] for desc in cur.description]
    results: list[MailRecord] = []
    for row in rows:
        results.append(row_to_mailrecord(row, col_names))
    return results
