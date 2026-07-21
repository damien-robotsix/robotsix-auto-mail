"""LLM-driven draft-reply generation for a single ingested mail.

Given a stored ``MailRecord`` this module asks the LLM to prepare a
concise, professional reply in the same language as the incoming mail,
persists it via :func:`robotsix_auto_mail.db.update_draft_text`, and
returns the draft text.  No email is ever sent — this only prepares and
stores a draft for the user to review and edit on the board.

The ``pydantic_ai`` / LLM-provider imports are lazy (inside
:func:`generate_draft_reply`) so this module imports cleanly without the
optional LLM extra, mirroring :mod:`robotsix_auto_mail.triage`.
"""

from __future__ import annotations

import sqlite3

from pydantic import BaseModel

from robotsix_auto_mail.core._llm_agent import _run_llm_agent
from robotsix_auto_mail.db import (
    MailRecord,
    get_record_by_message_id,
    update_draft_text,
)
from robotsix_auto_mail.errors import RobotsixMailError

#: Upper bound on the body length fed to the LLM, to control token cost.
_BODY_CHAR_LIMIT = 8000


class DraftGenerationError(RobotsixMailError):
    """Raised when draft-reply generation fails."""


class DraftResult(BaseModel):
    """Structured LLM output — the drafted reply body."""

    draft_text: str


def _build_draft_system_prompt() -> str:
    """Return the system prompt instructing the model how to draft a reply."""
    return (
        "You are an email-drafting assistant. You are given a single "
        "incoming email (sender, recipients, subject, and body). Write a "
        "concise, professional reply to it.\n"
        "\n"
        "Rules:\n"
        "- Write the reply in the SAME LANGUAGE as the incoming email.\n"
        "- Address the reply to the sender of the incoming email.\n"
        "- Do NOT invent facts, commitments, dates, numbers, or details "
        "that are not present in the incoming email. When a specific detail "
        "the user must supply is needed, leave a clear `[placeholder]` "
        "marker describing what to fill in (e.g. `[your availability]`).\n"
        "- The user message MAY include a `User notes / instructions` "
        "section written by the person who will send this reply. Treat "
        "these notes as authoritative guidance and follow them. Facts and "
        "decisions explicitly stated in the notes are NOT considered "
        "invented — use the details the user supplied there.\n"
        "- Keep the tone polite and professional.\n"
        "- End with a neutral sign-off placeholder (e.g. `[Your name]`).\n"
        "\n"
        "Return a JSON object with a single `draft_text` field containing "
        "the reply body. Return ONLY the JSON object matching the schema — "
        "no explanation, no markdown fences."
    )


def _build_draft_user_message(record: MailRecord) -> str:
    """Render *record* as the user message describing the mail to reply to."""
    body = record.body_plain
    if not body or not body.strip():
        # Fall back to a stripped form of the HTML body when plaintext is
        # empty.  Plaintext is the primary field; keep this simple.
        body = " ".join(record.body_html.split())
    body = body[:_BODY_CHAR_LIMIT]
    message = (
        f"Subject: {record.subject}\n"
        f"From: {record.sender}\n"
        f"Recipients: {record.recipients_json}\n"
        "\n"
        f"Body:\n{body}"
    )
    if record.notes.strip():
        message += (
            "\n\nUser notes / instructions (from the person who will send "
            f"this reply — follow them):\n{record.notes}"
        )
    return message


def generate_draft_reply(
    conn: sqlite3.Connection,
    message_id: str,
    *,
    api_key: str | None = None,
    provider_model: str | None = None,
    level: int = 1,
) -> str:
    """Generate, persist, and return an LLM draft reply for *message_id*.

    Fetches the ``MailRecord`` for *message_id*, asks the LLM to draft a
    reply, stores the result with
    :func:`robotsix_auto_mail.db.update_draft_text`, and returns the draft
    string.  The triage decision is intentionally NOT changed here — column
    movement is left to the server handler so the DB layer stays UI-agnostic.

    Args:
        conn: Open SQLite connection.
        message_id: The ``mail_records`` message id to draft a reply for.
        api_key: OpenRouter API key.  Resolves with the precedence
            ``api_key`` argument → ``LLM_API_KEY`` env var → config file (via
            :func:`~robotsix_auto_mail.core._llm_agent._run_llm_agent`).
        provider_model: LLM provider-model identifier (e.g. ``openrouter-deepseek``).
            ``None`` falls back to the tier-level default model (via
            :func:`~robotsix_auto_mail.core._llm_agent._run_llm_agent`).
        level: LLM integer tier to use.  ``1`` (cheap, default).

    Raises:
        DraftGenerationError: If no record exists for *message_id* or the
            LLM call fails.
    """
    record = get_record_by_message_id(conn, message_id)
    if record is None:
        raise DraftGenerationError(f"no record for message_id {message_id}")

    user_message = _build_draft_user_message(record)

    output = _run_llm_agent(
        api_key=api_key,
        provider_model=provider_model,
        level=level,
        system_prompt=_build_draft_system_prompt(),
        output_model=DraftResult,
        user_message=user_message,
        label="mail draft",
        what="mail draft",
        exc_type=DraftGenerationError,
    )

    draft = output.draft_text
    update_draft_text(conn, message_id, draft)
    return draft
