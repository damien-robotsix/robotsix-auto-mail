"""Tests for GET /chat-skill — chat-access standard compliance."""

from __future__ import annotations

from urllib.request import urlopen

import yaml
from robotsix_http.fastapi import (
    ChatSkillFrontmatter,
    parse_chat_skill_frontmatter,
)

from robotsix_auto_mail.server._constants import _STATIC_CHAT_SKILL_MD
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
        assert isinstance(frontmatter, dict), (
            f"frontmatter is not a dict: {frontmatter}"
        )
        assert "name" in frontmatter, f"missing 'name' in frontmatter: {frontmatter}"
        assert "description" in frontmatter, (
            f"missing 'description' in frontmatter: {frontmatter}"
        )
        assert frontmatter["name"] == "robotsix-auto-mail"
        assert isinstance(frontmatter["description"], str)
        assert len(frontmatter["description"]) > 0
    finally:
        server.shutdown()


def test_static_chat_skill_md_satisfies_shared_parser() -> None:
    """The served descriptor satisfies the shared chat-access contract.

    auto-mail serves ``/chat-skill`` via stdlib ``http.server`` (not FastAPI),
    so it cannot mount ``robotsix_http.fastapi.create_chat_skill_router``.  This
    test validates ``_STATIC_CHAT_SKILL_MD`` against the same framework-agnostic
    ``parse_chat_skill_frontmatter`` the FastAPI repos enforce via that router,
    guaranteeing the descriptor meets the identical contract: a ``---``-delimited
    frontmatter block with a kebab-case ``name`` and a non-empty one-sentence
    ``description``.
    """
    frontmatter = parse_chat_skill_frontmatter(_STATIC_CHAT_SKILL_MD)

    assert isinstance(frontmatter, ChatSkillFrontmatter)
    # Kebab-case component id, matching auto-mail's package/component name.
    assert frontmatter.name == "robotsix-auto-mail"
    # A non-empty, single-sentence description (the parser rejects an empty one).
    assert frontmatter.description
    assert frontmatter.description.count(".") <= 1
