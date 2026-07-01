"""Auto-mail-specific schema migration functions.

The additive "add-column-if-missing" helpers (:func:`add_column_if_missing`
and :func:`run_additive_migrations`) are provided by the fleet-shared
``robotsix_llmio.core.sqlite_utils`` module; import them directly from there.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from .models import VALID_TRIAGE_ACTIONS

#: Mirror of :data:`robotsix_auto_mail.db.models._TRIAGE_ACTION_CHECK_VALUES` —
#: the ``IN (...)`` value list for the ``triage_decisions.action`` CHECK
#: constraint, derived from :data:`VALID_TRIAGE_ACTIONS` so that a rebuilt
#: table always reflects the live vocabulary and future additions self-heal.
_TRIAGE_ACTION_CHECK_VALUES = ", ".join(repr(a) for a in sorted(VALID_TRIAGE_ACTIONS))


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
    return datetime.now(UTC).isoformat()


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


def _migrate_triage_action_check(conn: sqlite3.Connection) -> None:
    """Rebuild ``triage_decisions`` when its stored CHECK constraint is stale.

    SQLite bakes the ``action IN (...)`` CHECK vocabulary into the table at
    ``CREATE TABLE`` time and offers no ``ALTER`` to change a CHECK.  A DB
    created before a new action was added to :data:`VALID_TRIAGE_ACTIONS`
    therefore keeps rejecting that action forever, even though
    ``init_db``'s ``CREATE TABLE IF NOT EXISTS`` is a no-op on the existing
    table.  This migration detects that drift by reading the stored DDL from
    ``sqlite_master`` and — when any current action is missing — rebuilds the
    table with the up-to-date constraint, copying every row across.

    The vocabulary is derived from :data:`VALID_TRIAGE_ACTIONS`
    (via :data:`_TRIAGE_ACTION_CHECK_VALUES`) so future additions self-heal.

    Idempotent: when the stored DDL already permits every current action the
    function returns without touching the table.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'triage_decisions'"
    ).fetchone()
    if row is None or row[0] is None:
        return
    stored_ddl: str = row[0]
    if all(repr(action) in stored_ddl for action in VALID_TRIAGE_ACTIONS):
        return

    # SQLite cannot ALTER a CHECK constraint, so rebuild the table:
    # create-with-current-DDL, copy rows, drop old, rename.  ``PRAGMA
    # foreign_keys`` must be toggled outside any transaction, so commit any
    # pending work first, then wrap the rebuild itself in a transaction.
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF;")
    try:
        conn.execute(
            f"""
CREATE TABLE triage_decisions_new (
    message_id  TEXT NOT NULL UNIQUE,
    action      TEXT NOT NULL CHECK(action IN (
                    {_TRIAGE_ACTION_CHECK_VALUES}
                )),
    source      TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    confidence  TEXT NOT NULL DEFAULT 'medium',
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES mail_records(message_id)
)
"""
        )
        conn.execute(
            "INSERT INTO triage_decisions_new "
            "(message_id, action, source, reason, confidence, updated_at) "
            "SELECT message_id, action, source, reason, confidence, updated_at "
            "FROM triage_decisions"
        )
        conn.execute("DROP TABLE triage_decisions")
        conn.execute("ALTER TABLE triage_decisions_new RENAME TO triage_decisions")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON;")
