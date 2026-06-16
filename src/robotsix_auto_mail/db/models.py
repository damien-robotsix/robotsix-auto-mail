"""Data model and DDL schema for the local SQLite mail datastore.

Provides ``MailRecord`` — a frozen dataclass that defines the shape of a
stored mail message — together with the canonical triage vocabulary,
the multi-table DDL schema, and the additive column definitions used
by the migration layer.
"""

from __future__ import annotations

import dataclasses

#: Canonical triage state vocabulary — the six kanban columns.
#: ``INBOX`` means "not triaged" (no ``triage_decisions`` row, or an
#: explicit reset).
VALID_TRIAGE_ACTIONS = frozenset(
    {
        "INBOX",
        "HUMAN_TRIAGE",
        "PENDING_ACTION",
        "TO_ARCHIVE",
        "TO_DELETE",
        "TO_CALENDAR",
        "TO_ANSWER",
        "DRAFT_READY",
    }
)

#: The default status assigned to new ``MailRecord`` instances and used as
#: the SQL DDL default.  Must be one of the valid kanban statuses
#: (``needs_reply``, ``waiting``, ``to_read``, ``no_action``, ``done``).
#: Newly-ingested mail lands in the "To read" column.
DEFAULT_STATUS: str = "to_read"

#: SQL fragment for the triage_decisions CHECK constraint, derived
#: from :data:`VALID_TRIAGE_ACTIONS` so that the DDL and the runtime
#: vocabulary cannot drift apart.
_TRIAGE_ACTION_CHECK_VALUES = ", ".join(repr(a) for a in sorted(VALID_TRIAGE_ACTIONS))

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
    source_folder: str = "INBOX"
    recipients_json: str = '{"to": [], "cc": []}'
    body_plain: str = ""
    body_html: str = ""
    attachments_json: str = "[]"
    unsubscribe_header: str = ""
    notes: str = ""
    draft_text: str = ""
    sent_reply_text: str = ""
    calendar_event_ref: str = ""
    calendar_correlation_id: str = ""

    id: int = 0  # assigned by DB; ignored on insert


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS mail_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    imap_uid        INTEGER,
    source_folder   TEXT    NOT NULL DEFAULT 'INBOX',
    message_id      TEXT    NOT NULL UNIQUE,
    sender          TEXT    NOT NULL,
    subject         TEXT    NOT NULL,
    date            TEXT    NOT NULL,
    recipients_json TEXT    NOT NULL,
    body_plain      TEXT    NOT NULL,
    body_html       TEXT    NOT NULL,
    attachments_json TEXT   NOT NULL,
    unsubscribe_header TEXT NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT '{DEFAULT_STATUS}',
    notes           TEXT    NOT NULL DEFAULT '',
    draft_text      TEXT    NOT NULL DEFAULT '',
    sent_reply_text TEXT    NOT NULL DEFAULT '',
    calendar_event_ref TEXT NOT NULL DEFAULT '',
    calendar_correlation_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS watermark (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS triage_decisions (
    message_id  TEXT NOT NULL UNIQUE,
    action      TEXT NOT NULL CHECK(action IN (
                    {_TRIAGE_ACTION_CHECK_VALUES}
                )),
    source      TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    confidence  TEXT NOT NULL DEFAULT 'medium',
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES mail_records(message_id)
);
"""


#: Additive ``mail_records`` columns applied at startup for legacy DBs that
#: predate them.  Each entry is a column DDL fragment passed to
#: :func:`._migrate.run_additive_migrations`; the fragments are byte-for-byte
#: equivalent to the column definitions previously added by the per-column
#: ``_migrate_add_*`` functions, in the same order.
_ADDITIVE_COLUMNS: tuple[str, ...] = (
    "unsubscribe_header TEXT NOT NULL DEFAULT ''",
    "notes TEXT NOT NULL DEFAULT ''",
    "draft_text TEXT NOT NULL DEFAULT ''",
    "sent_reply_text TEXT NOT NULL DEFAULT ''",
    "source_folder TEXT NOT NULL DEFAULT 'INBOX'",
    "calendar_event_ref TEXT NOT NULL DEFAULT ''",
    "calendar_correlation_id TEXT NOT NULL DEFAULT ''",
)
