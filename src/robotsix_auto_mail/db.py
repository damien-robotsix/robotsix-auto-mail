"""Local SQLite datastore for ingested mail messages and watermark tracking.

Provides ``MailRecord`` — a frozen dataclass that defines the shape of a
stored mail message — and a handful of functions for initialising the
database, inserting records idempotently, and managing a key-value
watermark store (used by the IMAP fetch layer to track the last-seen
UID).
"""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

#: The default status assigned to new ``MailRecord`` instances and used as
#: the SQL DDL default.  Must be a member of
#: :data:`robotsix_auto_mail.status.STATUS_ORDER`.  Newly-ingested mail lands
#: in the "To read" column.
DEFAULT_STATUS: str = "to_read"

#: One-time remap of legacy workflow-internal status values to the new
#: awaiting-action column set.  Applied at startup by :func:`init_db` so no
#: row is left with an invalid status.  ``done`` is unchanged and therefore
#: omitted.
_LEGACY_STATUS_MIGRATION: dict[str, str] = {
    "inbox": "to_read",
    "triaging": "needs_reply",
    "archive": "no_action",
}

# ---------------------------------------------------------------------------
# MailRecord
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class MailRecord:
    """One ingested mail message, ready for storage.

    The ``id`` field is assigned by the database on insert (auto-increment
    primary key).  Before the first insert it is always ``0``.
    """

    message_id: str
    sender: str
    subject: str
    date: str

    status: str = DEFAULT_STATUS
    imap_uid: int | None = None
    recipients_json: str = '{"to": [], "cc": []}'
    body_plain: str = ""
    body_html: str = ""
    attachments_json: str = "[]"

    id: int = 0  # assigned by DB; ignored on insert


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS mail_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    imap_uid        INTEGER,
    message_id      TEXT    NOT NULL UNIQUE,
    sender          TEXT    NOT NULL,
    subject         TEXT    NOT NULL,
    date            TEXT    NOT NULL,
    recipients_json TEXT    NOT NULL,
    body_plain      TEXT    NOT NULL,
    body_html       TEXT    NOT NULL,
    attachments_json TEXT   NOT NULL,
    status          TEXT    NOT NULL DEFAULT '{DEFAULT_STATUS}'
);

CREATE TABLE IF NOT EXISTS watermark (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS triage_decisions (
    message_id  TEXT NOT NULL UNIQUE,
    action      TEXT NOT NULL CHECK(action IN (
                    'INBOX', 'HUMAN_TRIAGE', 'TO_ARCHIVE', 'TO_DELETE', 'TO_ANSWER'
                )),
    source      TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    confidence  TEXT NOT NULL DEFAULT 'medium',
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES mail_records(message_id)
);
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_db(
    path: str,
    *,
    skip_migrations: bool = False,
) -> sqlite3.Connection:
    """Open (or create) the SQLite database at *path* and set up the schema.

    Enables WAL journal mode and foreign-key enforcement.  The caller
    owns the returned connection and must close it.

    The parent directory is created if needed, so a path like
    ``.data/mail.db`` works on a fresh checkout.  The special
    ``":memory:"`` path is left untouched.

    When *skip_migrations* is ``True`` only the schema is ensured;
    the three startup migration functions are not called.  This is
    useful for read-only rendering code paths (e.g. the kanban board)
    that should not trigger repeated data migrations.
    """
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    if not skip_migrations:
        _migrate_legacy_statuses(conn)
        _migrate_status_to_triage(conn)
    return conn


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
            (action, _utc_now_iso(), old_status),
        )
    conn.commit()


def insert_record(conn: sqlite3.Connection, record: MailRecord) -> int | None:
    """Insert *record* into ``mail_records``.

    Returns the new ``rowid`` on success, or ``None`` when a row with
    the same ``message_id`` already exists (UNIQUE conflict).  The
    ``id`` field of the input ``MailRecord`` is ignored — the database
    assigns it.
    """
    try:
        cur = conn.execute(
            """\
INSERT INTO mail_records
    (imap_uid, message_id, sender, subject, date,
     recipients_json, body_plain, body_html, attachments_json, status)
VALUES
    (:imap_uid, :message_id, :sender, :subject, :date,
     :recipients_json, :body_plain, :body_html, :attachments_json, :status)
""",
            {
                "imap_uid": record.imap_uid,
                "message_id": record.message_id,
                "sender": record.sender,
                "subject": record.subject,
                "date": record.date,
                "recipients_json": record.recipients_json,
                "body_plain": record.body_plain,
                "body_html": record.body_html,
                "attachments_json": record.attachments_json,
                "status": record.status,
            },
        )
    except sqlite3.IntegrityError:
        # UNIQUE constraint on message_id violated — record already
        # exists; return None to signal idempotent skip.
        return None
    else:
        conn.commit()
        return cur.lastrowid


def get_record_by_message_id(
    conn: sqlite3.Connection,
    message_id: str,
) -> MailRecord | None:
    """Return the ``MailRecord`` for *message_id*, or ``None`` if not found.

    Read-only — does **not** call ``conn.commit()``.
    """
    cur = conn.execute("SELECT * FROM mail_records WHERE message_id = ?", (message_id,))
    row = cur.fetchone()
    if row is None:
        return None
    col_names = [desc[0] for desc in cur.description]
    return row_to_mailrecord(row, col_names)


def record_exists(conn: sqlite3.Connection, message_id: str) -> bool:
    """Check whether a message with *message_id* already exists in the DB.

    Used as a fast pre-check before ``insert_record`` to avoid
    triggering and catching ``IntegrityError`` in the hot path.
    The ``UNIQUE`` constraint remains the last-resort guard.
    """
    cur = conn.execute(
        "SELECT 1 FROM mail_records WHERE message_id = ? LIMIT 1",
        (message_id,),
    )
    return cur.fetchone() is not None


def delete_record_by_message_id(conn: sqlite3.Connection, message_id: str) -> bool:
    """Delete a mail record and its triage decision by *message_id*.

    Deletes from ``triage_decisions`` first (due to the foreign-key
    constraint referencing ``mail_records``), then from ``mail_records``.

    Returns ``True`` if a ``mail_records`` row was deleted, ``False``
    if no matching record existed.
    """
    conn.execute(
        "DELETE FROM triage_decisions WHERE message_id = ?",
        (message_id,),
    )
    cur = conn.execute(
        "DELETE FROM mail_records WHERE message_id = ?",
        (message_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def get_watermark(conn: sqlite3.Connection, key: str) -> str | None:
    """Return the watermark value for *key*, or ``None`` if it hasn't been set."""
    cur = conn.execute("SELECT value FROM watermark WHERE key = ?", (key,))
    row = cur.fetchone()
    return row[0] if row is not None else None


def row_to_mailrecord(
    row: tuple,  # type: ignore[type-arg]  # sqlite3 rows are dynamic at the type level
    col_names: list[str],
) -> MailRecord:
    """Convert a sqlite3 row tuple and its column names to a ``MailRecord``.

    The caller is responsible for extracting *col_names* from
    ``cursor.description``.  This helper is pure — it does not touch
    the cursor or the connection — so both ``db.list_records`` and
    ``status.list_by_status`` can share it.
    """
    data = dict(zip(col_names, row, strict=True))
    return MailRecord(
        message_id=data["message_id"],
        sender=data["sender"],
        subject=data["subject"],
        date=data["date"],
        status=data["status"],
        imap_uid=data["imap_uid"],
        recipients_json=data["recipients_json"],
        body_plain=data["body_plain"],
        body_html=data["body_html"],
        attachments_json=data["attachments_json"],
        id=data["id"],
    )


def list_records(conn: sqlite3.Connection) -> list[MailRecord]:
    """Return every ``MailRecord`` in the ``mail_records`` table.

    Rows are returned in the order the database provides (typically
    insertion order given the auto-increment primary key).  The caller
    owns the connection and must close it.
    """
    cur = conn.execute("SELECT * FROM mail_records ORDER BY id ASC")
    rows = cur.fetchall()
    col_names = [desc[0] for desc in cur.description]
    results: list[MailRecord] = []
    for row in rows:
        results.append(row_to_mailrecord(row, col_names))
    return results


def list_untriaged_records(conn: sqlite3.Connection) -> list[MailRecord]:
    """Return every ``MailRecord`` without a ``triage_decisions`` row.

    Uses a ``LEFT JOIN … WHERE td.message_id IS NULL`` so untriaged
    records are returned in ``id ASC`` order.  Read-only — does **not**
    call ``conn.commit()``.
    """
    cur = conn.execute(
        """\
SELECT mr.*
FROM mail_records mr
LEFT JOIN triage_decisions td ON mr.message_id = td.message_id
WHERE td.message_id IS NULL
ORDER BY mr.id ASC
"""
    )
    rows = cur.fetchall()
    col_names = [desc[0] for desc in cur.description]
    results: list[MailRecord] = []
    for row in rows:
        results.append(row_to_mailrecord(row, col_names))
    return results


def set_watermark(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert a watermark value.

    If *key* already exists its value is updated; otherwise a new row
    is inserted.
    """
    conn.execute(
        """\
INSERT INTO watermark (key, value) VALUES (?, ?)
ON CONFLICT(key) DO UPDATE SET value = excluded.value
""",
        (key, value),
    )
    conn.commit()
