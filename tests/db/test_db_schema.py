"""Tests for schema CHECK constraint sync with VALID_TRIAGE_ACTIONS."""

from __future__ import annotations

from robotsix_auto_mail.db import (
    _SCHEMA,
    _TRIAGE_ACTION_CHECK_VALUES,
    VALID_TRIAGE_ACTIONS,
)

# ---------------------------------------------------------------------------
# Schema generation: CHECK constraint stays in sync with VALID_TRIAGE_ACTIONS
# ---------------------------------------------------------------------------


def test_triage_action_check_sql_matches_frozenset() -> None:
    """The generated CHECK SQL fragment includes every triage action."""
    # The fragment must appear inside the _SCHEMA DDL.
    assert _TRIAGE_ACTION_CHECK_VALUES in _SCHEMA

    # Every canonical action must be represented as a quoted string.
    for action in VALID_TRIAGE_ACTIONS:
        assert repr(action) in _TRIAGE_ACTION_CHECK_VALUES

    # The fragment must contain exactly the right number of items.
    assert _TRIAGE_ACTION_CHECK_VALUES.count(",") == len(VALID_TRIAGE_ACTIONS) - 1
