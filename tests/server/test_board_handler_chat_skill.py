"""Tests for GET /chat-skill — chat-access standard compliance."""

from __future__ import annotations

from urllib.request import urlopen

import yaml

from tests.server.conftest_helpers import _start_test_server


def test_chat_skill_returns_200(single_db: str) -> None:
    """GET /chat-skill returns 200 and text/markdown."""
    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/chat-skill")
        assert resp.status == 200
        content_type = resp.headers.get("Content-Type", "")
        assert "text/markdown" in content_type
    finally:
        server.shutdown()


def test_chat_skill_content_type_is_markdown() -> None:
    """GET /chat-skill Content-Type is text/markdown."""
    server, port = _start_test_server(":memory:")
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/chat-skill")
        content_type = resp.headers.get("Content-Type", "")
        assert "text/markdown" in content_type
    finally:
        server.shutdown()


def test_chat_skill_has_frontmatter(single_db: str) -> None:
    """GET /chat-skill body has valid YAML frontmatter with name + description."""
    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/chat-skill")
        body = resp.read().decode("utf-8")
        # The body must start with YAML frontmatter delimited by ---
        assert body.startswith("---\n"), f"missing opening ---: {body[:80]}"
        parts = body.split("---\n", 2)
        assert len(parts) >= 3, f"expected frontmatter + body, got {len(parts)} parts"
        frontmatter_text = parts[1]
        frontmatter = yaml.safe_load(frontmatter_text)
        assert isinstance(frontmatter, dict), f"frontmatter is not a dict: {frontmatter}"
        assert "name" in frontmatter, f"missing 'name' in frontmatter: {frontmatter}"
        assert "description" in frontmatter, f"missing 'description' in frontmatter: {frontmatter}"
        assert frontmatter["name"] == "robotsix-auto-mail"
        assert isinstance(frontmatter["description"], str)
        assert len(frontmatter["description"]) > 0
    finally:
        server.shutdown()
