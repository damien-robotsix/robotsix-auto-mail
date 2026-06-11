"""Static triage constants shared across the triage submodules.

Kept in a dependency-free leaf module (no intra-package imports beyond
:mod:`robotsix_auto_mail.db`) so the persistence / classifier / agent
submodules can share them without risking circular imports.
"""

from __future__ import annotations

from robotsix_auto_mail.db import VALID_TRIAGE_ACTIONS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Canonical triage action order for the kanban board, left-to-right.
#: Starts with ``INBOX`` (not triaged / entry column) and ends with
#: ``TO_ANSWER`` (action needed).
TRIAGE_ACTION_ORDER: tuple[str, ...] = (
    "INBOX",
    "HUMAN_TRIAGE",
    "PENDING_ACTION",
    "TO_ARCHIVE",
    "TO_DELETE",
    "TO_ANSWER",
    "DRAFT_READY",
)

#: Human-readable column header labels, keyed by triage action.
TRIAGE_ACTION_LABELS: dict[str, str] = {
    "INBOX": "Inbox",
    "HUMAN_TRIAGE": "Human triage",
    "PENDING_ACTION": "Pending action",
    "TO_ARCHIVE": "To archive",
    "TO_DELETE": "To delete",
    "TO_ANSWER": "To answer",
    "DRAFT_READY": "Draft ready",
}

#: Actions the LLM triage agent may assign.  ``INBOX`` is intentionally
#: excluded: it is reserved for not-yet-triaged mail (the board entry
#: column).  When the agent is unsure it must use ``HUMAN_TRIAGE`` rather
#: than parking mail back in the inbox, so undecided mail always surfaces
#: in the Human-triage column instead of silently staying in Inbox.  Users
#: may still move a card to Inbox manually; this constraint applies only to
#: agent-generated decisions.
_AGENT_SELECTABLE_ACTIONS: frozenset[str] = VALID_TRIAGE_ACTIONS - {
    "INBOX",
    "DRAFT_READY",
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

#: Accepted confidence levels (mirrors ``DriftProposal.confidence``).
_VALID_CONFIDENCE_LEVELS = frozenset({"low", "medium", "high"})

#: Accepted :class:`TriageRule` match types.
_VALID_RULE_MATCH_TYPES = frozenset({"sender", "domain", "subject_contains"})

#: Accepted :class:`RuleLedgerEntry` states.  All three suppress re-proposal
#: of a rule once it has been recorded (mirrors ``config_sync_agent`` vocabulary).
_VALID_RULE_STATES = frozenset({"pending", "accepted", "rejected"})

#: Watermark key owned by this module for the rule-proposal dedup ledger.
#: Persisted in ``db.py``'s ``watermark`` key-value table as JSON (no new
#: tables / files), mirroring :mod:`robotsix_auto_mail.config.config_sync_agent`.
_RULE_LEDGER_WATERMARK_KEY = "triage_rules_ledger"

#: Watermark key owned by this module for the accepted (active) rules list.
_RULE_ACTIVE_WATERMARK_KEY = "triage_rules_active"

#: Minimum number of consistent, non-``HUMAN_TRIAGE`` decisions before a rule
#: is proposed for a sender (or, in total, for a domain).
_RULE_MIN_DECISIONS = 3

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
