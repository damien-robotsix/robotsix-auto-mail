"""Tests for the archive proposal GET endpoint."""

from __future__ import annotations

import json
import urllib.error
from urllib.request import urlopen

import pytest

from tests.server.conftest_helpers import (
    _populate_db,
    _seed_archive_override,
    _seed_archive_structure,
    _seed_triage_decision,
    _start_test_server,
)


def test_archive_proposal_endpoint_returns_json(single_db: str) -> None:
    """GET /archive-proposal/<mid> returns expected JSON shape."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "ap-mid",
                "sender": "alice@example.com",
                "subject": "Archive me",
                "date": "2025-06-01T12:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "ap-mid", action="TO_ARCHIVE")
    _seed_archive_structure(
        single_db,
        ["my-archive", "my-archive/Lists/dev"],
    )

    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/archive-proposal/ap-mid")
        assert resp.status == 200
        assert resp.headers.get("Content-Type", "").startswith("application/json")
        body = resp.read().decode("utf-8")
        payload = json.loads(body)
        assert "subfolder" in payload
        assert "archive_root" in payload
        assert "folder_exists" in payload
        assert "overridden" in payload
        assert "source" in payload
        # Either "rule" (deterministic) or "llm" (no hint stored)
        assert payload["source"] in ("rule", "llm", "override")
        assert isinstance(payload["folder_exists"], bool)
        assert isinstance(payload["overridden"], bool)
    finally:
        server.shutdown()


def test_archive_proposal_endpoint_with_override(single_db: str) -> None:
    """GET /archive-proposal/<mid> returns source='override' when override exists."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "ap-override",
                "sender": "a@b.com",
                "subject": "Test",
                "date": "2025-06-01T12:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "ap-override", action="TO_ARCHIVE")
    _seed_archive_override(single_db, "ap-override", "Custom/Path")

    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/archive-proposal/ap-override")
        payload = json.loads(resp.read().decode("utf-8"))
        assert payload["subfolder"] == "Custom/Path"
        assert payload["source"] == "override"
        assert payload["overridden"] is True
    finally:
        server.shutdown()


def test_archive_proposal_endpoint_unknown_404() -> None:
    """GET /archive-proposal/unknown → 404."""
    server, port = _start_test_server(":memory:")
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urlopen(f"http://127.0.0.1:{port}/archive-proposal/nonexistent")
        assert exc_info.value.code == 404
    finally:
        server.shutdown()
