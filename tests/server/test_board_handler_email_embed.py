"""Tests for the board handler (HTTP request routing and board rendering)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.request import urlopen

import pytest

if TYPE_CHECKING:
    pass

from tests.server.conftest import (
    _populate_db,
    _start_test_server,
)

# ---------------------------------------------------------------------------
# GET /email/{message_id}?embed=1 handler integration tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GET /email/{message_id}?embed=1 handler integration tests
# ---------------------------------------------------------------------------


def test_handler_email_detail_embed_returns_fragment(single_db: str) -> None:
    """GET /email/{id}?embed=1 returns HTML fragment without full-page chrome."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "embed-handler@test.com",
                "sender": "eh@test.com",
                "subject": "Embed Handler",
                "date": "2025-01-01T00:00:00",
                "body_plain": "embed handler body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/email/embed-handler@test.com?embed=1")
        assert resp.status == 200
        body = resp.read().decode("utf-8")
        # Fragment — no full-page chrome
        assert "<!DOCTYPE html>" not in body
        assert "<html" not in body
        assert "<title>" not in body
        # But has the content
        assert "eh@test.com" in body
        assert "embed handler body" in body
        assert 'class="embed-detail"' in body
        # Move form with redirect_to
        assert 'name="redirect_to"' in body
    finally:
        server.shutdown()


def test_handler_email_detail_embed_unknown_returns_404() -> None:
    """GET /email/unknown?embed=1 returns 404 (same as non-embed)."""
    server, port = _start_test_server(":memory:")
    try:
        import urllib.error

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urlopen(f"http://127.0.0.1:{port}/email/does-not-exist?embed=1")
        assert exc_info.value.code == 404
    finally:
        server.shutdown()
