"""Tests for IngestError and IngestResult dataclasses."""

from __future__ import annotations

import dataclasses

import pytest

from robotsix_auto_mail.pipeline import IngestError, IngestResult


# ---------------------------------------------------------------------------
# IngestError / IngestResult dataclass tests
# ---------------------------------------------------------------------------


def test_ingest_error_is_frozen() -> None:
    err = IngestError(uid=1, message_id="<x@y>", error="boom")
    assert err.uid == 1
    assert err.message_id == "<x@y>"
    assert err.error == "boom"
    with pytest.raises(dataclasses.FrozenInstanceError):
        err.uid = 2  # type: ignore[misc]


def test_ingest_error_empty_message_id() -> None:
    err = IngestError(uid=5, message_id="", error="parse failed")
    assert err.message_id == ""


def test_ingest_result_is_frozen() -> None:
    result = IngestResult(total_fetched=3, stored=2, skipped=1, errors=[])
    assert result.total_fetched == 3
    assert result.stored == 2
    assert result.skipped == 1
    assert result.errors == []
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.stored = 99  # type: ignore[misc]


def test_ingest_result_defaults() -> None:
    result = IngestResult(total_fetched=0, stored=0, skipped=0, errors=[])
    assert result.total_fetched == 0
    assert result.errors == []
