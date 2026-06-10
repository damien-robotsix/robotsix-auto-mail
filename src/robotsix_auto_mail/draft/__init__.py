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
from robotsix_llmio.core import Tier

from robotsix_auto_mail.config import load_llm
from robotsix_auto_mail.db import (
    MailRecord,
    get_record_by_message_id,
    update_draft_text,
)

#: Upper bound on the body length fed to the LLM, to control token cost.
_BODY_CHAR_LIMIT = 8000


class DraftGenerationError(Exception):
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
    return (
        f"Subject: {record.subject}\n"
        f"From: {record.sender}\n"
        f"Recipients: {record.recipients_json}\n"
        "\n"
        f"Body:\n{body}"
    )


def generate_draft_reply(
    conn: sqlite3.Connection,
    message_id: str,
    *,
    api_key: str | None = None,
    tier: Tier = Tier.CHEAP,
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
            ``api_key`` argument → ``config.load_llm()``.
        tier: LLM tier to use.  ``Tier.CHEAP`` (default).

    Raises:
        DraftGenerationError: If no record exists for *message_id* or the
            LLM call fails.
    """
    record = get_record_by_message_id(conn, message_id)
    if record is None:
        raise DraftGenerationError(f"no record for message_id {message_id}")

    # -- lazy imports so the module loads without pydantic_ai installed --
    from pydantic_ai import PromptedOutput
    from robotsix_llmio.core import start_trace
    from robotsix_llmio.openrouter_deepseek import OpenRouterDeepseekProvider

    # -- resolve API key (arg -> config.load_llm()) --
    resolved_key = api_key
    if not resolved_key:
        resolved_key, _ = load_llm()
    if not resolved_key:
        raise DraftGenerationError(
            "No LLM API key found — set the LLM_API_KEY environment "
            "variable or add an `llm.api_key` entry to your config file"
        )

    user_message = _build_draft_user_message(record)

    try:
        llm_provider = OpenRouterDeepseekProvider(api_key=resolved_key)
        agent_handle = llm_provider.build_agent(
            tier=tier,
            system_prompt=_build_draft_system_prompt(),
            output_type=PromptedOutput(DraftResult),
        )
        try:
            with start_trace("mail draft") as trace:
                trace.set_input(user_message)
                result = llm_provider.call_with_retry(
                    lambda: agent_handle.run_sync(user_message),
                    what="mail draft",
                )
                trace.set_output(str(result.output))
        finally:
            agent_handle.close()
    except DraftGenerationError:
        raise
    except Exception as exc:
        raise DraftGenerationError(str(exc)) from exc

    output: DraftResult = result.output
    draft = output.draft_text
    update_draft_text(conn, message_id, draft)
    return draft
