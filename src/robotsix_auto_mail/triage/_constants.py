"""Static triage constants shared across the triage submodules.

Kept in a dependency-free leaf module (no intra-package imports beyond
:mod:`robotsix_auto_mail.db`) so the persistence / classifier / agent
submodules can share them without risking circular imports.
"""

from __future__ import annotations

from robotsix_auto_mail.config.pydantic_utils import (
    VALID_CONFIDENCE_LEVELS as _VALID_CONFIDENCE_LEVELS,
)
from robotsix_auto_mail.db import VALID_TRIAGE_ACTIONS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Triage action: mail not yet triaged (entry column on the board).
INBOX: str = "INBOX"
#: Triage action: needs human review.
HUMAN_TRIAGE: str = "HUMAN_TRIAGE"
#: Triage action: pending user action.
PENDING_ACTION: str = "PENDING_ACTION"
#: Triage action: ready to archive.
TO_ARCHIVE: str = "TO_ARCHIVE"
#: Triage action: ready to delete.
TO_DELETE: str = "TO_DELETE"
#: Triage action: ready for calendar.
TO_CALENDAR: str = "TO_CALENDAR"
#: Triage action: ready to answer.
TO_ANSWER: str = "TO_ANSWER"
#: Triage action: draft is ready for sending.
DRAFT_READY: str = "DRAFT_READY"

#: Canonical triage action order for the kanban board, left-to-right.
#: Starts with ``INBOX`` (not triaged / entry column) and ends with
#: ``TO_ANSWER`` (action needed).
TRIAGE_ACTION_ORDER: tuple[str, ...] = (
    INBOX,
    HUMAN_TRIAGE,
    PENDING_ACTION,
    TO_ARCHIVE,
    TO_DELETE,
    TO_CALENDAR,
    TO_ANSWER,
    DRAFT_READY,
)

#: Human-readable column header labels, keyed by triage action.
TRIAGE_ACTION_LABELS: dict[str, str] = {
    INBOX: "Inbox",
    HUMAN_TRIAGE: "Human triage",
    PENDING_ACTION: "Pending action",
    TO_ARCHIVE: "To archive",
    TO_DELETE: "To delete",
    TO_CALENDAR: "To calendar",
    TO_ANSWER: "To answer",
    DRAFT_READY: "Draft ready",
}

#: Actions the LLM triage agent may assign.  ``INBOX`` is intentionally
#: excluded: it is reserved for not-yet-triaged mail (the board entry
#: column).  When the agent is unsure it must use ``HUMAN_TRIAGE`` rather
#: than parking mail back in the inbox, so undecided mail always surfaces
#: in the Human-triage column instead of silently staying in Inbox.  Users
#: may still move a card to Inbox manually; this constraint applies only to
#: agent-generated decisions.
_AGENT_SELECTABLE_ACTIONS: frozenset[str] = VALID_TRIAGE_ACTIONS - {
    INBOX,
    DRAFT_READY,
    PENDING_ACTION,
    TO_CALENDAR,
}

#: Accepted decision sources.
_VALID_TRIAGE_SOURCES = frozenset({"agent", "user"})

#: Watermark key owned by this module for the persistent human-decision
#: memory.  The memory is persisted in ``db.py``'s ``watermark`` key-value
#: table — NOT a separate on-disk file — using the same ``json.dumps`` /
#: ``json.loads`` round-trip :mod:`robotsix_auto_mail.config.config_sync_agent` uses for
#: its dedup ledger.  Reusing the watermark table keeps a single storage
#: mechanism and a single DB file instead of a parallel format.
_MEMORY_WATERMARK_KEY = "triage_human_memory"

#: Watermark key owned by this module for user archive subfolder overrides.
_ARCHIVE_OVERRIDES_WATERMARK_KEY = "archive_subfolder_overrides"

#: Watermark key owned by this module for LLM-suggested archive subfolders.
_ARCHIVE_LLM_HINTS_WATERMARK_KEY = "archive_subfolder_llm_hints"

#: Watermark key owned by this module for the archive-folder memory — which
#: subfolder a sender's / domain's mail has been filed into.  Persisted in
#: ``db.py``'s ``watermark`` key-value table as JSON (no new tables / files),
#: so the proposal can prefer reusing an established project folder.
_ARCHIVE_FOLDER_MEMORY_WATERMARK_KEY = "archive_folder_memory"

#: Watermark key owned by this module for unsubscribe-suggestion cache.
_UNSUBSCRIBE_SUGGESTIONS_KEY = "unsubscribe_suggestions"

# ---------------------------------------------------------------------------
# Explicit re-exports (required by mypy --strict / --no-implicit-reexport)
# ---------------------------------------------------------------------------
__all__ = [
    "DRAFT_READY",
    "HUMAN_TRIAGE",
    "INBOX",
    "PENDING_ACTION",
    "TO_ANSWER",
    "TO_ARCHIVE",
    "TO_CALENDAR",
    "TO_DELETE",
    "TRIAGE_ACTION_LABELS",
    "TRIAGE_ACTION_ORDER",
    "_AGENT_SELECTABLE_ACTIONS",
    "_ARCHIVE_FOLDER_MEMORY_WATERMARK_KEY",
    "_ARCHIVE_LLM_HINTS_WATERMARK_KEY",
    "_ARCHIVE_OVERRIDES_WATERMARK_KEY",
    "_MEMORY_WATERMARK_KEY",
    "_UNSUBSCRIBE_SUGGESTIONS_KEY",
    "_VALID_CONFIDENCE_LEVELS",
    "_VALID_TRIAGE_SOURCES",
]
