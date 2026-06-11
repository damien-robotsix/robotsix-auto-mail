"""Pydantic models and SQLite persistence for triage decisions.

Holds the structured LLM-output contract models and the
``triage_decisions`` table CRUD helpers.  Imports only
:mod:`robotsix_auto_mail.db`, ``pydantic`` and the shared triage
constants — it is the leaf the classifier and agent submodules build on.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pydantic

from robotsix_auto_mail.db import VALID_TRIAGE_ACTIONS
from robotsix_auto_mail.triage._constants import (
    _AGENT_SELECTABLE_ACTIONS,
    _VALID_CONFIDENCE_LEVELS,
    _VALID_RULE_MATCH_TYPES,
    _VALID_RULE_STATES,
    _VALID_TRIAGE_SOURCES,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TriageError(Exception):
    """Raised when the inbox triage agent or persistence layer fails."""


# ---------------------------------------------------------------------------
# Pydantic models — structured LLM output contract
# ---------------------------------------------------------------------------


class TriageItem(pydantic.BaseModel):
    """One classified mail in the LLM response, referenced by 1-based index."""

    index: int = pydantic.Field(..., ge=1)
    #: Triage action.  Unknown / empty values — and ``INBOX``, which the
    #: agent is not allowed to assign — are coerced to ``HUMAN_TRIAGE``
    #: rather than failing the whole batch.
    action: str = pydantic.Field(default="HUMAN_TRIAGE")
    reason: str = pydantic.Field(default="")
    #: Confidence level — one of ``low`` / ``medium`` / ``high``.
    confidence: str = pydantic.Field(default="medium")
    #: Optional LLM-suggested archive subfolder path relative to the
    #: archive root (e.g. ``Lists/python-dev``).  Only meaningful when
    #: ``action`` is ``TO_ARCHIVE``; ignored otherwise.
    archive_subfolder: str = pydantic.Field(default="")

    @pydantic.field_validator("action")
    @classmethod
    def _coerce_action(cls, v: str) -> str:
        if v not in _AGENT_SELECTABLE_ACTIONS:
            return "HUMAN_TRIAGE"
        return v

    @pydantic.field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, v: str) -> str:
        if v not in _VALID_CONFIDENCE_LEVELS:
            raise ValueError(
                "confidence must be one of "
                f"{sorted(_VALID_CONFIDENCE_LEVELS)!r}; got {v!r}"
            )
        return v


class TriageResult(pydantic.BaseModel):
    """Structured output the LLM must return — validated by pydantic.

    An empty ``items`` list is valid; any omitted inbox record is defaulted
    to ``HUMAN_TRIAGE`` by :func:`run_triage_agent`.
    """

    items: list[TriageItem] = pydantic.Field(default_factory=list)


class TriageDecision(pydantic.BaseModel):
    """A stored triage decision for a single mail, keyed by ``message_id``."""

    message_id: str
    action: str
    #: Who recorded the decision — ``agent`` or ``user``.
    source: str
    reason: str = ""
    confidence: str = "medium"

    @pydantic.field_validator("action")
    @classmethod
    def _validate_action(cls, v: str) -> str:
        if v not in VALID_TRIAGE_ACTIONS:
            raise ValueError(
                f"action must be one of {sorted(VALID_TRIAGE_ACTIONS)!r}; got {v!r}"
            )
        return v

    @pydantic.field_validator("source")
    @classmethod
    def _validate_source(cls, v: str) -> str:
        if v not in _VALID_TRIAGE_SOURCES:
            raise ValueError(
                f"source must be one of {sorted(_VALID_TRIAGE_SOURCES)!r}; got {v!r}"
            )
        return v


class UnsubscribeDetection(pydantic.BaseModel):
    """LLM-detected unsubscribe mechanism for a sender.

    Cached in the ``unsubscribe_suggestions`` watermark and rendered
    as an info banner on the board's TO_DELETE column.
    """

    has_unsubscribe: bool
    #: Detection method — ``header`` / ``body_link`` / ``mailto`` / ``none``.
    method: str = ""
    #: Unsubscribe URL or ``mailto:`` address.
    url: str = ""
    #: Short human-readable summary for the board banner.
    description: str = ""
    #: Confidence level — ``low`` / ``medium`` / ``high``.
    confidence: str = "medium"


class SenderMemory(pydantic.BaseModel):
    """One sender's remembered human-triage preference.

    Stored in the human-decision memory ledger keyed by the lowercased
    sender email.  ``action`` is the most recent human action for the
    sender, ``last_action`` is the action recorded immediately before this
    one (equal to ``action`` for a brand-new entry), ``count`` is how many
    times the user has triaged mail from this sender and ``updated_at`` is
    the ISO-8601 UTC timestamp of the latest update.
    """

    action: str
    count: int = 1
    last_action: str = ""
    updated_at: str = ""

    @pydantic.field_validator("action", "last_action")
    @classmethod
    def _validate_action(cls, v: str) -> str:
        if v and v not in VALID_TRIAGE_ACTIONS:
            raise ValueError(
                f"action must be one of {sorted(VALID_TRIAGE_ACTIONS)!r}; got {v!r}"
            )
        return v


class ArchiveSubfolderProposal(pydantic.BaseModel):
    """Structured LLM output for a per-mail archive subfolder proposal."""

    subfolder: str = pydantic.Field(
        default="",
        description=(
            "Proposed archive subfolder path relative to the archive root "
            "(e.g. 'Lists/python-dev' or 'Receipts/2025').  "
            "Empty string if no suitable folder can be determined."
        ),
    )


class TriageRule(pydantic.BaseModel):
    """A deterministic triage rule mapping a match condition to an action.

    Matching is intentionally limited to three simple, non-regex kinds
    (``match_type``): ``sender`` (exact lowercased email address),
    ``domain`` (the sender's domain) and ``subject_contains`` (a
    case-insensitive subject substring).  ``action`` is validated against
    :data:`VALID_TRIAGE_ACTIONS`.
    """

    match_type: str
    match_value: str
    action: str

    @pydantic.field_validator("match_type")
    @classmethod
    def _validate_match_type(cls, v: str) -> str:
        if v not in _VALID_RULE_MATCH_TYPES:
            raise ValueError(
                "match_type must be one of "
                f"{sorted(_VALID_RULE_MATCH_TYPES)!r}; got {v!r}"
            )
        return v

    @pydantic.field_validator("action")
    @classmethod
    def _validate_action(cls, v: str) -> str:
        if v not in VALID_TRIAGE_ACTIONS:
            raise ValueError(
                f"action must be one of {sorted(VALID_TRIAGE_ACTIONS)!r}; got {v!r}"
            )
        return v


class TriageRuleProposal(TriageRule):
    """A :class:`TriageRule` plus human-readable presentation fields.

    Carries a ``title`` / ``body`` for display and a ``confidence`` level
    (``low`` / ``medium`` / ``high``) reflecting the strength of the
    evidence.  Its identity (fingerprint) is derived solely from the
    underlying rule's ``(match_type, match_value, action)``.
    """

    title: str = pydantic.Field(..., min_length=1)
    body: str = pydantic.Field(..., min_length=1)
    confidence: str = pydantic.Field(default="medium")

    @pydantic.field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, v: str) -> str:
        if v not in _VALID_CONFIDENCE_LEVELS:
            raise ValueError(
                "confidence must be one of "
                f"{sorted(_VALID_CONFIDENCE_LEVELS)!r}; got {v!r}"
            )
        return v


class RuleLedgerEntry(pydantic.BaseModel):
    """One remembered rule proposal in the dedup memory ledger.

    Keyed (in the ledger dict) by the rule's stable fingerprint.  The
    ``match_type`` / ``match_value`` / ``action`` fields preserve the
    underlying :class:`TriageRule` so accepting a proposal can reconstruct
    it for the active-rules list.  The ``state`` (``pending`` / ``accepted``
    / ``rejected``) suppresses re-proposal in every state.
    """

    match_type: str
    match_value: str
    action: str
    title: str = ""
    state: str = "pending"

    @pydantic.field_validator("state")
    @classmethod
    def _validate_state(cls, v: str) -> str:
        if v not in _VALID_RULE_STATES:
            raise ValueError(
                f"state must be one of {sorted(_VALID_RULE_STATES)!r}; got {v!r}"
            )
        return v


# ---------------------------------------------------------------------------
# Persistence helpers — triage_decisions table
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def set_triage_decision(
    conn: sqlite3.Connection,
    message_id: str,
    action: str,
    *,
    source: str,
    reason: str = "",
    confidence: str = "medium",
) -> None:
    """Upsert a triage decision for *message_id*.

    Validates *action* against :data:`VALID_TRIAGE_ACTIONS` and *source*
    against ``{"agent", "user"}`` (raising :class:`TriageError` otherwise),
    then upserts keyed on ``message_id`` and commits.  ``updated_at`` is set
    to an ISO-8601 UTC timestamp.
    """
    if action not in VALID_TRIAGE_ACTIONS:
        raise TriageError(
            f"action must be one of {sorted(VALID_TRIAGE_ACTIONS)!r}; got {action!r}"
        )
    if source not in _VALID_TRIAGE_SOURCES:
        raise TriageError(
            f"source must be one of {sorted(_VALID_TRIAGE_SOURCES)!r}; got {source!r}"
        )
    conn.execute(
        """\
INSERT INTO triage_decisions
    (message_id, action, source, reason, confidence, updated_at)
VALUES
    (:message_id, :action, :source, :reason, :confidence, :updated_at)
ON CONFLICT(message_id) DO UPDATE SET
    action = excluded.action,
    source = excluded.source,
    reason = excluded.reason,
    confidence = excluded.confidence,
    updated_at = excluded.updated_at
""",
        {
            "message_id": message_id,
            "action": action,
            "source": source,
            "reason": reason,
            "confidence": confidence,
            "updated_at": _utc_now_iso(),
        },
    )
    conn.commit()


def get_triage_decision(
    conn: sqlite3.Connection, message_id: str
) -> TriageDecision | None:
    """Return the stored :class:`TriageDecision` for *message_id*, or ``None``.

    Read-only — does **not** call ``conn.commit()``.
    """
    cur = conn.execute(
        "SELECT message_id, action, source, reason, confidence "
        "FROM triage_decisions WHERE message_id = ?",
        (message_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return TriageDecision(
        message_id=row[0],
        action=row[1],
        source=row[2],
        reason=row[3],
        confidence=row[4],
    )


def list_triage_decisions(
    conn: sqlite3.Connection, *, source: str | None = None
) -> list[TriageDecision]:
    """Return all stored triage decisions, ordered by ``message_id``.

    When *source* is given, only decisions with that source are returned.
    Read-only — does **not** call ``conn.commit()``.
    """
    if source is None:
        cur = conn.execute(
            "SELECT message_id, action, source, reason, confidence "
            "FROM triage_decisions ORDER BY message_id ASC"
        )
    else:
        cur = conn.execute(
            "SELECT message_id, action, source, reason, confidence "
            "FROM triage_decisions WHERE source = ? ORDER BY message_id ASC",
            (source,),
        )
    return [
        TriageDecision(
            message_id=row[0],
            action=row[1],
            source=row[2],
            reason=row[3],
            confidence=row[4],
        )
        for row in cur.fetchall()
    ]


def delete_triage_decisions_by_action(conn: sqlite3.Connection, action: str) -> int:
    """Delete all triage decision rows for *action*.

    :param conn: SQLite connection (must be writable).
    :param action: A member of ``VALID_TRIAGE_ACTIONS`` (case-sensitive).
        Must not be ``"INBOX"`` — inbox records have no triage decision
        row by definition.
    :returns: Number of rows deleted.
    :raises TriageError: If *action* is not a valid triage action.
    :raises TriageError: If *action* is ``"INBOX"``.
    """
    if action not in VALID_TRIAGE_ACTIONS:
        raise TriageError(f"Invalid triage action: {action!r}")
    if action == "INBOX":
        raise TriageError(
            "Cannot delete triage decisions for action='INBOX': "
            "INBOX records have no triage decision rows."
        )
    cur = conn.execute("DELETE FROM triage_decisions WHERE action = ?", (action,))
    conn.commit()
    return cur.rowcount
