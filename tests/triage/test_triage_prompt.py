"""Tests for _build_triage_system_prompt with archive folder prompts."""

from __future__ import annotations

import json

from robotsix_auto_mail.db import init_db, set_watermark
from robotsix_auto_mail.triage import (
    _build_triage_system_prompt,
    _load_archive_folders,
)

# ---------------------------------------------------------------------------
# _build_triage_system_prompt with archive folders
# ---------------------------------------------------------------------------


def test_system_prompt_with_archive_folders() -> None:
    """When archive_folders is non-empty, the prompt includes folder list."""
    prompt = _build_triage_system_prompt(
        archive_folders=["robotsix-mail-archive", "robotsix-mail-archive/Lists/dev"]
    )
    assert "existing sub-folders" in prompt
    assert "robotsix-mail-archive" in prompt
    assert "robotsix-mail-archive/Lists/dev" in prompt
    assert "archive_subfolder" in prompt
    assert "TO_ARCHIVE" in prompt


def test_system_prompt_without_archive_folders() -> None:
    """When archive_folders is None, prompt is unchanged."""
    prompt = _build_triage_system_prompt(archive_folders=None)
    assert "existing sub-folders" not in prompt
    assert "archive_subfolder" not in prompt


def test_system_prompt_with_empty_archive_folders() -> None:
    """When archive_folders is empty, no archive section is appended."""
    prompt = _build_triage_system_prompt(archive_folders=[])
    assert "existing sub-folders" not in prompt


def test_system_prompt_with_user_email() -> None:
    """When user_email is set, the prompt names it and forbids self-answers."""
    prompt = _build_triage_system_prompt(user_email="me@example.com")
    assert "me@example.com" in prompt
    assert "reply to yourself" in prompt
    assert "TO_ANSWER" in prompt


def test_system_prompt_without_user_email_unchanged() -> None:
    """Without user_email the prompt is byte-identical to the no-arg form."""
    prompt = _build_triage_system_prompt(user_email=None)
    assert "reply to yourself" not in prompt
    assert prompt == _build_triage_system_prompt()


def test_system_prompt_taxonomy_guidance_present() -> None:
    """A non-empty folder list emits purpose/topic taxonomy guidance, the
    bare domain/sender prohibition, and the ≤2-level depth cap."""
    prompt = _build_triage_system_prompt(archive_folders=["Newsletters/LWN"])
    lower = prompt.lower()
    # Purpose/topic categorization guidance.
    assert "purpose" in lower
    assert "topic" in lower
    # Explicit prohibition of bare domain/sender paths.
    assert "do not use bare" in lower
    assert "domain" in lower
    assert "sender" in lower
    # Shallow depth cap stated explicitly.
    assert "at most 2 levels" in prompt


def test_system_prompt_folders_rendered_plain() -> None:
    """The folder list renders as plain ``- <folder>`` bullets, with no
    usage-count or history annotations."""
    prompt = _build_triage_system_prompt(["Lists/python-dev", "Finance", "Travel"])
    assert "- Lists/python-dev\n" in prompt
    assert "- Finance\n" in prompt
    assert "- Travel\n" in prompt
    assert "(used" not in prompt
    assert "Archive-folder history" not in prompt


# ---------------------------------------------------------------------------
# _load_archive_folders
# ---------------------------------------------------------------------------


def test_load_archive_folders_normalises_structure() -> None:
    """The persisted ``archive_structure`` list is loaded and normalised into
    a plain list of folder paths."""
    conn = init_db(":memory:")
    try:
        set_watermark(
            conn,
            "archive_structure",
            json.dumps(["Newsletters/LWN", "Projects/armada"]),
        )
        folders = _load_archive_folders(conn)
        assert folders == ["Newsletters/LWN", "Projects/armada"]
    finally:
        conn.close()


def test_load_archive_folders_absent_is_none() -> None:
    """With no persisted structure, ``_load_archive_folders`` returns None."""
    conn = init_db(":memory:")
    try:
        assert _load_archive_folders(conn) is None
    finally:
        conn.close()
