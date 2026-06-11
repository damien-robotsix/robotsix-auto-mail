"""LLM-driven inbox triage agent.

Prompt building, per-sender unsubscribe detection and the
``run_triage_agent`` driver.  The ``pydantic_ai`` /
``OpenRouterDeepseekProvider`` imports stay lazy inside the agent
functions to keep module-load time low.
"""

from __future__ import annotations

import json
import os
import sqlite3

from robotsix_llmio.core import Tier, run_agent

from robotsix_auto_mail.config import load_llm
from robotsix_auto_mail.db import (
    MailRecord,
    get_record_by_message_id,
    get_watermark,
    list_untriaged_records,
    set_watermark,
)
from robotsix_auto_mail.format import _BODY_PREVIEW_LIMIT
from robotsix_auto_mail.triage._constants import (
    _AGENT_SELECTABLE_ACTIONS,
    _UNSUBSCRIBE_SUGGESTIONS_KEY,
)
from robotsix_auto_mail.triage.classifier import (
    _build_memory_guidance,
    _load_llm_archive_hints,
    _save_llm_archive_hints,
    _sender_key,
    apply_triage_rules,
)
from robotsix_auto_mail.triage.persistence import (
    TriageDecision,
    TriageError,
    TriageItem,
    TriageResult,
    UnsubscribeDetection,
    get_triage_decision,
    list_triage_decisions,
    set_triage_decision,
)

# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def _build_triage_system_prompt(
    archive_folders: list[str] | None = None,
) -> str:
    """Build the LLM system prompt describing the triage task and actions.

    When *archive_folders* is a non-empty list, appends a paragraph
    describing the available archive folders and the optional
    ``archive_subfolder`` field.
    """
    prompt = (
        "You are an inbox triage assistant. You are given a numbered list of "
        "incoming mail messages, each with a 1-based index, sender, subject, "
        "and a short body preview. Classify each message into exactly one "
        "action status:\n"
        "\n"
        "- `HUMAN_TRIAGE`: you are NOT confident what to do — defer to a "
        "human.\n"
        "- `TO_ARCHIVE`: keep the message for reference but no reply is "
        "needed.\n"
        "- `TO_DELETE`: the message is junk / worthless and can be "
        "discarded.\n"
        "- `TO_ANSWER`: the message needs a personal reply.\n"
        "\n"
        "Reference each message by its 1-based `index` (do NOT echo back any "
        "message id). For each message return an `index`, an `action` (one "
        "of the values above), a short `reason`, and a `confidence` of "
        "`low`, `medium`, or `high`. Prefer `HUMAN_TRIAGE` over guessing.\n"
        "\n"
        "Return a JSON object with an `items` list. Return ONLY the JSON "
        "object matching the schema — no explanation, no markdown fences.\n"
        "\n"
        "When a message is marked as already answered (a reply has already "
        "been sent), it should normally be classified `TO_ARCHIVE`, unless "
        "the reply content indicates the thread still needs further action."
    )
    if archive_folders:
        folder_lines = "\n".join(f"- {f}" for f in archive_folders)
        prompt += (
            "\n\nThe user has an archive folder structure with these "
            "existing sub-folders:\n"
            f"{folder_lines}\n\n"
            "When you classify a message as `TO_ARCHIVE`, you may "
            "optionally set an `archive_subfolder` field with a suggested "
            "destination path relative to the archive root (e.g. "
            "`Lists/python-dev`). You may suggest an existing folder from "
            "the list above, or propose a new folder name that fits the "
            "message. Leave the field empty or omit it when you have no "
            "suggestion.\n"
        )
    return prompt


def _body_preview(body: str) -> str:
    """Return a single-line body preview truncated to ``_BODY_PREVIEW_LIMIT``."""
    collapsed = " ".join(body.split())
    if len(collapsed) > _BODY_PREVIEW_LIMIT:
        return collapsed[:_BODY_PREVIEW_LIMIT] + "…"
    return collapsed


def _build_user_message(records: list) -> str:  # type: ignore[type-arg]
    """Enumerate *records* as ``index | sender | subject | <body preview>``."""
    lines = ["Messages to triage (index | sender | subject | body preview):"]
    for i, record in enumerate(records, start=1):
        line = (
            f"{i} | {record.sender} | {record.subject} | "
            f"{_body_preview(record.body_plain)}"
        )
        if record.sent_reply_text:
            line += f" | ANSWERED — reply sent: {_body_preview(record.sent_reply_text)}"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core agent
# ---------------------------------------------------------------------------


def _detect_unsubscribe_for_sender(
    conn: sqlite3.Connection | None,
    sender: str,
    records: list[MailRecord],
) -> UnsubscribeDetection | None:
    """Check *sender* (lowercased address) for unsubscribe options.

    Picks the most recent record by ``date``.  If its
    ``unsubscribe_header`` is non-empty, returns a mechanical detection
    without calling the LLM.  Otherwise calls the LLM to scan the full
    ``body_plain`` for unsubscribe links / instructions.

    Returns ``None`` on LLM failure so the caller can continue safely.
    """
    # Pick the most recent record by date (descending), or fall back to
    # the last record in the list.
    sorted_records = sorted(records, key=lambda r: r.date or "", reverse=True)
    recent = sorted_records[0] if sorted_records else records[-1]

    # -- mechanical fast path: List-Unsubscribe header present -----------
    header = (recent.unsubscribe_header or "").strip()
    # Strip angle brackets (RFC 2369 format: <mailto:...> or <https://...>).
    if header.startswith("<") and header.endswith(">"):
        header = header[1:-1].strip()
    if header:
        method = "mailto" if header.lower().startswith("mailto:") else "header"
        return UnsubscribeDetection(
            has_unsubscribe=True,
            method=method,
            url=header,
            description=(
                "List-Unsubscribe header found"
                if method == "header"
                else "mailto unsubscribe address found in header"
            ),
            confidence="high",
        )

    # -- LLM path: scan body for unsubscribe options --------------------

    # Resolve API key with the same precedence as run_triage_agent.
    resolved_key = os.environ.get("LLM_API_KEY", "")
    if not resolved_key:
        resolved_key, _ = load_llm()
    if not resolved_key:
        return None

    from pydantic_ai import PromptedOutput
    from robotsix_llmio.openrouter_deepseek import OpenRouterDeepseekProvider

    system_prompt = (
        "You are an unsubscribe-detection assistant. "
        "Analyze the email body and determine if there is an unsubscribe "
        "mechanism — a URL, a mailto link, or natural-language instructions "
        "(e.g. 'reply with UNSUBSCRIBE'). "
        "Return a structured result with:\n"
        "- has_unsubscribe: true/false\n"
        "- method: one of 'body_link', 'mailto', or 'none'\n"
        "- url: the unsubscribe URL or mailto address (empty if none)\n"
        "- description: a short human-readable summary\n"
        "- confidence: 'low', 'medium', or 'high'"
    )

    user_message = (
        f"Sender: {recent.sender}\n"
        f"Subject: {recent.subject}\n\n"
        f"Body:\n{recent.body_plain}"
    )

    llm_provider = OpenRouterDeepseekProvider(api_key=resolved_key)
    agent_handle = llm_provider.build_agent(
        tier=Tier.CHEAP,
        system_prompt=system_prompt,
        output_type=PromptedOutput(UnsubscribeDetection),
    )

    try:
        result = run_agent(
            agent_handle,
            lambda: agent_handle.run_sync(user_message),
            label="unsubscribe detection",
            what="unsubscribe detection",
            trace_input=user_message,
        )
    except Exception:
        return None

    return result.output  # type: ignore[no-any-return]


def _check_unsubscribe_for_to_delete(conn: sqlite3.Connection) -> None:
    """Check TO_DELETE senders for unsubscribe options and cache findings.

    Groups TO_DELETE records by lowercased sender address.  For senders
    with ≥3 messages, runs :func:`_detect_unsubscribe_for_sender` and
    caches the result in the ``unsubscribe_suggestions`` watermark.
    Senders already cached are skipped (idempotent).
    """
    # Load all TO_DELETE decisions and resolve to MailRecords.
    all_decisions = list_triage_decisions(conn)
    to_delete = [d for d in all_decisions if d.action == "TO_DELETE"]
    if not to_delete:
        return

    # Resolve each decision to its MailRecord and group by sender key.
    by_sender: dict[str, list[MailRecord]] = {}
    for decision in to_delete:
        record = get_record_by_message_id(conn, decision.message_id)
        if record is None:
            continue
        key = _sender_key(record.sender)
        by_sender.setdefault(key, []).append(record)

    # Load existing suggestions cache.
    raw = get_watermark(conn, _UNSUBSCRIBE_SUGGESTIONS_KEY)
    suggestions: dict[str, object] = {}
    if raw is not None:
        try:
            suggestions = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            # Malformed suggestions-cache JSON — keep the empty dict above.
            pass

    updated = False
    for sender_key, sender_records in by_sender.items():
        if len(sender_records) < 3:
            continue
        if sender_key in suggestions:
            continue  # already cached
        detection = _detect_unsubscribe_for_sender(conn, sender_key, sender_records)
        if detection is not None:
            # Only cache if an unsubscribe mechanism was actually found.
            if detection.has_unsubscribe:
                suggestions[sender_key] = detection.model_dump()
                updated = True

    if updated:
        set_watermark(conn, _UNSUBSCRIBE_SUGGESTIONS_KEY, json.dumps(suggestions))


def run_triage_agent(
    conn: sqlite3.Connection,
    *,
    api_key: str | None = None,
    tier: Tier = Tier.CHEAP,
    only_undecided: bool = False,
) -> list[TriageDecision]:
    """Classify every inbox mail into a triage action and persist the result.

    Reads untriaged records via ``list_untriaged_records(conn)`` (every
    ``MailRecord`` without a ``triage_decisions`` row); returns
    ``[]`` immediately (without calling the LLM) when there is none.  Each
    returned ``TriageItem`` is mapped back to its ``MailRecord`` by 1-based
    index; unknown actions are clamped to ``HUMAN_TRIAGE`` and any omitted
    inbox record defaults to ``HUMAN_TRIAGE``.  Every decision is persisted
    with ``source='agent'``.

    Args:
        conn: Open SQLite connection.
        api_key: OpenRouter API key.  Resolves with the precedence
            ``api_key`` argument → ``LLM_API_KEY`` env var →
            ``config.llm_api_key`` (via :func:`load_llm`).
        tier: LLM tier to use.  ``Tier.CHEAP`` (default).
        only_undecided: When ``True``, inbox records that already have a
            ``triage_decisions`` row (per :func:`get_triage_decision`) are
            dropped before both the deterministic-rule fast-path and the
            LLM call, making the pass incremental and idempotent.  When the
            filtered set is empty, returns ``[]`` without building the LLM
            agent (no API key required).  Defaults to ``False`` (re-triage
            every inbox record, preserving the manual CLI behavior).

    Raises:
        TriageError: If the API key is missing or the LLM call fails.
    """
    records = list_untriaged_records(conn)
    if only_undecided:
        records = [
            record
            for record in records
            if get_triage_decision(conn, record.message_id) is None
        ]
    if not records:
        return []

    # -- deterministic rule fast-path: triage matching mail without the LLM --
    decisions: list[TriageDecision] = []
    remaining = []
    for record in records:
        matched_action = apply_triage_rules(conn, record)
        if matched_action is None:
            remaining.append(record)
            continue
        set_triage_decision(
            conn,
            record.message_id,
            matched_action,
            source="agent",
            reason="matched deterministic rule",
        )
        decisions.append(
            TriageDecision(
                message_id=record.message_id,
                action=matched_action,
                source="agent",
                reason="matched deterministic rule",
                confidence="medium",
            )
        )

    # Every inbox record was triaged deterministically — no LLM call needed.
    if not remaining:
        return decisions

    # -- resolve API key (arg -> LLM_API_KEY env -> config.llm_api_key) --
    resolved_key = api_key or os.environ.get("LLM_API_KEY", "")
    if not resolved_key:
        resolved_key, _ = load_llm()
    if not resolved_key:
        raise TriageError(
            "No LLM API key found — set the LLM_API_KEY environment "
            "variable or add an `llm.api_key` entry to your config file"
        )

    # -- read archive structure for the LLM system prompt ------------------

    archive_raw = get_watermark(conn, "archive_structure")
    archive_folders: list[str] | None = None
    if archive_raw is not None:
        try:
            data = json.loads(archive_raw)
            if isinstance(data, list):
                archive_folders = data
            else:
                archive_folders = data["folders"]
        except (json.JSONDecodeError, TypeError, KeyError):
            archive_folders = None

    # -- lazy imports so the rest of the CLI works without pydantic_ai --
    from pydantic_ai import PromptedOutput
    from robotsix_llmio.openrouter_deepseek import OpenRouterDeepseekProvider

    # -- build agent --
    llm_provider = OpenRouterDeepseekProvider(api_key=resolved_key)
    agent_handle = llm_provider.build_agent(
        tier=tier,
        system_prompt=_build_triage_system_prompt(archive_folders),
        output_type=PromptedOutput(TriageResult),
    )

    user_message = _build_user_message(remaining)

    # -- bias the model toward established human preferences (advisory) --
    guidance = _build_memory_guidance(conn)
    if guidance:
        user_message = f"{guidance}\n\n{user_message}"

    # -- call LLM --
    try:
        result = run_agent(
            agent_handle,
            lambda: agent_handle.run_sync(user_message),
            label="mail triage",
            what="mail triage",
            trace_input=user_message,
        )
    except Exception as exc:
        raise TriageError(str(exc)) from exc

    output: TriageResult = result.output

    # -- map 1-based indices back to records; default omissions to HUMAN_TRIAGE --
    by_index: dict[int, TriageItem] = {}
    for item in output.items:
        if 1 <= item.index <= len(remaining) and item.index not in by_index:
            by_index[item.index] = item

    for i, record in enumerate(remaining, start=1):
        matched = by_index.get(i)
        if matched is None:
            action, reason, confidence = "HUMAN_TRIAGE", "", "medium"
        else:
            action = matched.action
            if action not in _AGENT_SELECTABLE_ACTIONS:
                action = "HUMAN_TRIAGE"
            reason, confidence = matched.reason, matched.confidence
        set_triage_decision(
            conn,
            record.message_id,
            action,
            source="agent",
            reason=reason,
            confidence=confidence,
        )
        decisions.append(
            TriageDecision(
                message_id=record.message_id,
                action=action,
                source="agent",
                reason=reason,
                confidence=confidence,
            )
        )

    # -- store/clear LLM archive subfolder hints -------------------------

    hints = _load_llm_archive_hints(conn)
    # Clear stale hints: any remaining record whose LLM action is NOT
    # TO_ARCHIVE should have its hint entry removed.
    for i, record in enumerate(remaining, start=1):
        matched = by_index.get(i)
        if matched is not None and matched.action != "TO_ARCHIVE":
            hints.pop(record.message_id, None)
    # Upsert new hints for TO_ARCHIVE items with a non-empty subfolder.
    for i, record in enumerate(remaining, start=1):
        matched = by_index.get(i)
        if matched is not None and matched.action == "TO_ARCHIVE":
            sub = (matched.archive_subfolder or "").strip()
            if sub:
                hints[record.message_id] = sub
    _save_llm_archive_hints(conn, hints)

    # -- check TO_DELETE senders for unsubscribe options ------------------
    _check_unsubscribe_for_to_delete(conn)

    return decisions
