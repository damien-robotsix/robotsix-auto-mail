"""Thin, reusable helpers for additive SQLite schema migrations.

This module encapsulates the "add-column-if-missing, additive,
idempotent" idiom that ``init_db`` previously hand-rolled once per
column.  An additive migration adds a new column to an existing table
when it is missing and is a no-op when the column already exists.

The helpers here are intentionally **backend-agnostic in shape**: they
rely only on ``conn.execute(...)`` + ``conn.commit()`` and on catching
the driver's "duplicate column" :class:`sqlite3.OperationalError`.  That
makes them promotable verbatim into a fleet-shared library consumed by
both raw-``sqlite3`` and SQLAlchemy callers.  For now the ``conn``
parameter is typed as :class:`sqlite3.Connection` because auto-mail only
ever passes a raw connection; the module deliberately does not import or
couple to SQLAlchemy/SQLModel.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone


def add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column_ddl: str,
) -> bool:
    """Add a column to *table* if it does not already exist.

    Executes ``ALTER TABLE {table} ADD COLUMN {column_ddl}`` inside a
    ``try``/``except sqlite3.OperationalError`` block.  On success the
    change is committed and ``True`` is returned.  When the column
    already exists the driver raises ``sqlite3.OperationalError`` which
    is caught; ``False`` is returned without raising.

    *table* and *column_ddl* are internal, code-controlled constants
    (never user input), so the f-string interpolation into the DDL is
    safe here.

    The helper is backend-agnostic in shape — it relies only on
    ``conn.execute(...)`` + ``conn.commit()`` and on catching the
    driver's "duplicate column" ``OperationalError`` — so it can later
    be promoted into a fleet-shared library used by raw-``sqlite3`` and
    SQLAlchemy callers alike.
    """
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_ddl}")
        conn.commit()
    except sqlite3.OperationalError:
        return False
    else:
        return True


def run_additive_migrations(
    conn: sqlite3.Connection,
    table: str,
    column_ddls: Sequence[str],
) -> None:
    """Apply each additive column migration in *column_ddls* to *table*.

    Iterates *column_ddls* in order, calling :func:`add_column_if_missing`
    for each.  This sequencer replaces a waterfall of near-identical
    single-column migration functions; it is idempotent because each
    individual step is.
    """
    for column_ddl in column_ddls:
        add_column_if_missing(conn, table, column_ddl)


# ---------------------------------------------------------------------------
# Legacy status migrations (run by init_db at startup)
# ---------------------------------------------------------------------------

#: One-time remap of legacy workflow-internal status values to the new
#: awaiting-action column set.  Applied at startup by :func:`init_db` so no
#: row is left with an invalid status.  ``done`` is unchanged and therefore
#: omitted.
_LEGACY_STATUS_MIGRATION: dict[str, str] = {
    "inbox": "to_read",
    "triaging": "needs_reply",
    "archive": "no_action",
}


def _migrate_legacy_statuses(conn: sqlite3.Connection) -> None:
    """Remap any legacy ``mail_records.status`` values to the new column set.

    Idempotent: rows already using the new awaiting-action statuses are left
    untouched, so this is safe to run on every ``init_db`` call.
    """
    for old_status, new_status in _LEGACY_STATUS_MIGRATION.items():
        conn.execute(
            "UPDATE mail_records SET status = ? WHERE status = ?",
            (new_status, old_status),
        )
    conn.commit()


_STATUS_TO_TRIAGE_ACTION: dict[str, str] = {
    "needs_reply": "TO_ANSWER",
    "waiting": "INBOX",
    "no_action": "TO_ARCHIVE",
    "done": "TO_ARCHIVE",
}


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _migrate_status_to_triage(conn: sqlite3.Connection) -> None:
    """One-time migration: mirror non-default ``mail_records.status`` values
    into ``triage_decisions`` for rows that lack a decision.

    Only rows whose ``status`` is not ``"to_read"`` (the default) AND
    that have no corresponding row in ``triage_decisions`` are migrated.
    The mapping table is:

    - ``needs_reply → answer``
    - ``waiting → waiting``
    - ``no_action → archive``
    - ``done → ignore``

    Migrated rows are inserted with ``source="user"`` and
    ``reason="migrated from legacy status"``.  Rows already present in
    ``triage_decisions`` are skipped.  Idempotent: running multiple
    times never inserts duplicates.
    """
    now = _utc_now_iso()
    for old_status, action in _STATUS_TO_TRIAGE_ACTION.items():
        conn.execute(
            """\
INSERT OR IGNORE INTO triage_decisions
    (message_id, action, source, reason, confidence, updated_at)
SELECT mr.message_id, ?, 'user', 'migrated from legacy status', 'medium', ?
FROM mail_records mr
WHERE mr.status = ?
  AND mr.message_id NOT IN (
      SELECT message_id FROM triage_decisions
  )
""",
            (action, now, old_status),
        )
    conn.commit()
