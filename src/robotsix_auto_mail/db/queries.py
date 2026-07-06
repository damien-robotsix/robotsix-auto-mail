"""CRUD and query functions for the local SQLite mail datastore.

Provides functions for initialising the database, inserting records
idempotently, querying and updating records, managing watermark
key-value pairs, and converting sqlite3 rows to ``MailRecord``
instances.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from robotsix_llmio.core.sqlite_utils import run_additive_migrations

from ._migrate import (
    _migrate_legacy_statuses,
    _migrate_status_to_triage,
    _migrate_triage_action_check,
)
from .models import (
    _ADDITIVE_COLUMNS,
    _SCHEMA,
    MailRecord,
)

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
        _migrate_triage_action_check(conn)
        _migrate_legacy_statuses(conn)
        _migrate_status_to_triage(conn)
        run_additive_migrations(conn, "mail_records", _ADDITIVE_COLUMNS)  # type: ignore[arg-type]  # _SQLiteConn Protocol vs positional-only sqlite3.execute
    return conn


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
    (imap_uid, source_folder, message_id, sender, subject, date,
     recipients_json, body_plain, body_html, attachments_json,
     unsubscribe_header, status, notes, draft_text, sent_reply_text,
     calendar_event_ref, calendar_correlation_id)
VALUES
    (:imap_uid, :source_folder, :message_id, :sender, :subject, :date,
     :recipients_json, :body_plain, :body_html, :attachments_json,
     :unsubscribe_header, :status, :notes, :draft_text, :sent_reply_text,
     :calendar_event_ref, :calendar_correlation_id)
""",
            {
                "imap_uid": record.imap_uid,
                "source_folder": record.source_folder,
                "message_id": record.message_id,
                "sender": record.sender,
                "subject": record.subject,
                "date": record.date,
                "recipients_json": record.recipients_json,
                "body_plain": record.body_plain,
                "body_html": record.body_html,
                "attachments_json": record.attachments_json,
                "unsubscribe_header": record.unsubscribe_header,
                "status": record.status,
                "notes": record.notes,
                "draft_text": record.draft_text,
                "sent_reply_text": record.sent_reply_text,
                "calendar_event_ref": record.calendar_event_ref,
                "calendar_correlation_id": record.calendar_correlation_id,
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

    Tries the exact *message_id* first.  If no row matches, strips
    surrounding angle brackets (``<`` / ``>``) and retries.  If that
    also yields no match, adds angle brackets and retries one last
    time.  This makes the lookup resilient to callers that may or may
    not include the brackets.

    Read-only — does **not** call ``conn.commit()``.
    """
    candidates = [message_id]
    stripped = message_id.strip()
    if stripped.startswith("<") and stripped.endswith(">"):
        candidates.append(stripped[1:-1])
    else:
        candidates.append(f"<{stripped}>")
    for candidate in candidates:
        cur = conn.execute(
            "SELECT * FROM mail_records WHERE message_id = ?", (candidate,)
        )
        row = cur.fetchone()
        if row is not None:
            col_names = [desc[0] for desc in cur.description]
            return row_to_mailrecord(row, col_names)
    return None


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


def update_record_source(
    conn: sqlite3.Connection,
    message_id: str,
    *,
    source_folder: str,
    imap_uid: int | None = None,
) -> bool:
    """Update ``source_folder`` and ``imap_uid`` for an existing record.

    Used when a re-ingest from a named folder re-encounters a message_id whose
    tracked UID is stale — the row gets its source folder and UID
    refreshed so it becomes actionable for archive/delete.

    Returns ``True`` if a row was updated, ``False`` if no matching
    ``message_id`` exists.
    """
    cur = conn.execute(
        "UPDATE mail_records SET source_folder = ?, imap_uid = ? WHERE message_id = ?",
        (source_folder, imap_uid, message_id),
    )
    conn.commit()
    return cur.rowcount > 0


def update_notes(conn: sqlite3.Connection, message_id: str, notes: str) -> bool:
    """Update ``mail_records.notes`` for *message_id*.

    Returns ``True`` if a row was updated, ``False`` if no matching
    ``message_id`` exists.
    """
    cur = conn.execute(
        "UPDATE mail_records SET notes = ? WHERE message_id = ?",
        (notes, message_id),
    )
    conn.commit()
    return cur.rowcount > 0


def update_draft_text(
    conn: sqlite3.Connection, message_id: str, draft_text: str
) -> bool:
    """Update ``mail_records.draft_text`` for *message_id*.

    Returns ``True`` if a row was updated, ``False`` if no matching
    ``message_id`` exists.
    """
    cur = conn.execute(
        "UPDATE mail_records SET draft_text = ? WHERE message_id = ?",
        (draft_text, message_id),
    )
    conn.commit()
    return cur.rowcount > 0


def update_sent_reply_text(
    conn: sqlite3.Connection, message_id: str, text: str
) -> bool:
    """Update ``mail_records.sent_reply_text`` for *message_id*.

    Returns ``True`` if a row was updated, ``False`` if no matching
    ``message_id`` exists.
    """
    cur = conn.execute(
        "UPDATE mail_records SET sent_reply_text = ? WHERE message_id = ?",
        (text, message_id),
    )
    conn.commit()
    return cur.rowcount > 0


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
    the cursor or the connection — so ``db.list_records`` can share it.
    """
    data = dict(zip(col_names, row, strict=True))
    return MailRecord(
        message_id=data["message_id"],
        sender=data["sender"],
        subject=data["subject"],
        date=data["date"],
        status=data["status"],
        imap_uid=data["imap_uid"],
        source_folder=data["source_folder"],
        recipients_json=data["recipients_json"],
        body_plain=data["body_plain"],
        body_html=data["body_html"],
        attachments_json=data["attachments_json"],
        unsubscribe_header=data["unsubscribe_header"],
        notes=data["notes"],
        draft_text=data["draft_text"],
        sent_reply_text=data["sent_reply_text"],
        calendar_event_ref=data.get("calendar_event_ref", ""),
        calendar_correlation_id=data.get("calendar_correlation_id", ""),
        id=data["id"],
    )


def _rows_to_mailrecords(
    cur: sqlite3.Cursor,
    rows: list[tuple],  # type: ignore[type-arg]
) -> list[MailRecord]:
    """Convert fetched rows to ``MailRecord`` instances using column metadata."""
    col_names = [desc[0] for desc in cur.description]
    results: list[MailRecord] = []
    for row in rows:
        results.append(row_to_mailrecord(row, col_names))
    return results


def list_records(conn: sqlite3.Connection) -> list[MailRecord]:
    """Return every ``MailRecord`` in the ``mail_records`` table.

    Rows are returned in the order the database provides (typically
    insertion order given the auto-increment primary key).  The caller
    owns the connection and must close it.
    """
    cur = conn.execute("SELECT * FROM mail_records ORDER BY id ASC")
    rows = cur.fetchall()
    return _rows_to_mailrecords(cur, rows)


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
    return _rows_to_mailrecords(cur, rows)


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


def delete_watermark(conn: sqlite3.Connection, key: str) -> None:
    """Remove a watermark row so ``get_watermark`` returns ``None`` again.

    Used to reset a watermark that has become meaningless — e.g. the
    ``"imap_uid"`` watermark after the server's ``UIDVALIDITY`` changes,
    which puts UIDs into a fresh numbering space. Deleting (rather than
    zeroing) the key means the next fetch falls back to a full ``ALL``
    scan. No-op if the key is absent.
    """
    conn.execute("DELETE FROM watermark WHERE key = ?", (key,))
    conn.commit()


# ---------------------------------------------------------------------------
# Account health (watermark key ``"account_health"``)
# ---------------------------------------------------------------------------


def get_account_health(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Read ``"account_health"`` watermark; parse JSON; return ``None`` if absent."""
    raw = get_watermark(conn, "account_health")
    if raw is None:
        return None
    import json as _json

    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError, TypeError:
        return None
    if isinstance(data, dict):
        return data
    return None


def write_account_health(
    conn: sqlite3.Connection,
    *,
    status: str,  # "ok" | "failed"
    error: str | None,
    checked_at: str,  # ISO 8601 UTC
) -> None:
    """Upsert ``"account_health"`` watermark with a JSON payload."""
    import json as _json

    payload = _json.dumps({"status": status, "error": error, "checked_at": checked_at})
    set_watermark(conn, "account_health", payload)


