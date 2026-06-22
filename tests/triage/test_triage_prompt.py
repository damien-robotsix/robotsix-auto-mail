"""Tests for _build_triage_system_prompt with archive folder prompts."""

from __future__ import annotations

import json

from tests.conftest import _make_record

from robotsix_auto_mail.db import init_db, set_watermark
from robotsix_auto_mail.triage import (
    ArchiveFolderMemory,
    _build_triage_system_prompt,
    _is_non_semantic_subfolder,
    _load_archive_guidance,
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


def test_system_prompt_usage_counts_rendered() -> None:
    """A usage map annotates positive-count folders with ``(used Nx)`` and
    leaves zero/absent folders unannotated, with prefer-high-count guidance."""
    prompt = _build_triage_system_prompt(
        ["Lists/python-dev", "Finance", "Travel"],
        None,
        {"Lists/python-dev": 5, "Finance": 0},
    )
    assert "- Lists/python-dev (used 5x)" in prompt
    # Zero-count and absent folders render plain (no annotation).
    assert "- Finance\n" in prompt
    assert "- Travel\n" in prompt
    assert "Finance (used" not in prompt
    assert "Travel (used" not in prompt
    assert "prefer folders that already contain many mails" in prompt.lower()


def test_system_prompt_usage_counts_absent_renders_plain() -> None:
    """With no usage map the folder list renders as plain bullets."""
    prompt = _build_triage_system_prompt(["Finance", "Travel"])
    assert "- Finance\n" in prompt
    assert "(used" not in prompt


def test_is_non_semantic_subfolder() -> None:
    """Domain-like top-level segments are non-semantic; topical ones are not."""
    assert _is_non_semantic_subfolder("lwn.net/lwn") is True
    assert _is_non_semantic_subfolder("ls2n.fr/armada") is True
    assert _is_non_semantic_subfolder("Newsletters/LWN") is False
    assert _is_non_semantic_subfolder("Projects/armada") is False
    assert _is_non_semantic_subfolder("Finance") is False


def test_load_archive_guidance_weakens_non_semantic_history() -> None:
    """A remembered non-semantic ``domain/sender`` folder yields a weakened
    history hint, while a semantic folder keeps the authoritative wording."""
    conn = init_db(":memory:")
    try:
        set_watermark(
            conn,
            "archive_structure",
            json.dumps(["Newsletters/LWN", "Projects/armada"]),
        )
        from robotsix_auto_mail.triage import _save_archive_folder_memory

        _save_archive_folder_memory(
            conn,
            {
                "news@lwn.net": ArchiveFolderMemory(subfolder="lwn.net/lwn", count=2),
                "dev@armada.example": ArchiveFolderMemory(
                    subfolder="Projects/armada", count=4
                ),
            },
        )
        remaining = [
            _make_record(
                message_id="<a@lwn.net>",
                sender="news@lwn.net",
                subject="LWN weekly",
                date="2025-06-01T12:00:00",
            ),
            _make_record(
                message_id="<b@armada.example>",
                sender="dev@armada.example",
                subject="Armada update",
                date="2025-06-01T12:00:00",
            ),
        ]
        folders, history, usage = _load_archive_guidance(conn, remaining)
        prompt = _build_triage_system_prompt(folders, history or None, usage or None)

        lwn_line = next(line for line in history if "lwn.net/lwn" in line)
        armada_line = next(line for line in history if "Projects/armada" in line)
        assert "prefer a semantic topical folder over this domain/sender path" in (
            lwn_line
        )
        assert "prefer a semantic topical folder" not in armada_line
        # The weakened wording reaches the rendered prompt.
        assert "prefer a semantic topical folder over this domain/sender path" in prompt
    finally:
        conn.close()
