"""Tests for triage Pydantic models."""

from __future__ import annotations

import pydantic
import pytest

from robotsix_auto_mail.triage import (
    TriageDecision,
    TriageError,
    TriageItem,
    TriageResult,
)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


def test_triage_item_defaults() -> None:
    """action defaults to user_triage, confidence to medium, reason to ''."""
    item = TriageItem(index=1)
    assert item.action == "HUMAN_TRIAGE"
    assert item.confidence == "medium"
    assert item.reason == ""


def test_triage_item_coerces_unknown_action() -> None:
    """An unknown action is coerced to user_triage, not rejected."""
    item = TriageItem(index=1, action="banana")
    assert item.action == "HUMAN_TRIAGE"


def test_triage_item_coerces_inbox_action() -> None:
    """The agent may not assign INBOX; it is coerced to HUMAN_TRIAGE."""
    item = TriageItem(index=1, action="INBOX")
    assert item.action == "HUMAN_TRIAGE"


def test_triage_item_rejects_index_below_one() -> None:
    """index must be >= 1."""
    with pytest.raises(pydantic.ValidationError):
        TriageItem(index=0)


def test_triage_item_rejects_unknown_confidence() -> None:
    """An out-of-set confidence raises a pydantic ValidationError."""
    with pytest.raises(pydantic.ValidationError):
        TriageItem(index=1, confidence="bogus")


def test_triage_result_defaults_empty() -> None:
    """items defaults to an empty list."""
    assert TriageResult().items == []


def test_triage_decision_rejects_invalid_action() -> None:
    with pytest.raises(pydantic.ValidationError):
        TriageDecision(message_id="<a>", action="banana", source="user")


def test_triage_decision_accepts_draft_ready_action() -> None:
    """DRAFT_READY is a valid triage action."""
    decision = TriageDecision(
        message_id="<draft@test.com>", action="DRAFT_READY", source="user"
    )
    assert decision.action == "DRAFT_READY"


def test_triage_decision_rejects_invalid_source() -> None:
    with pytest.raises(pydantic.ValidationError):
        TriageDecision(message_id="<a>", action="TO_ANSWER", source="robot")


def test_triage_error_is_exception() -> None:
    err = TriageError("boom")
    assert isinstance(err, Exception)
    assert str(err) == "boom"
