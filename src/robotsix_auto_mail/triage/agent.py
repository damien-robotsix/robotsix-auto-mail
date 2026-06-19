"""LLM-driven inbox triage agent.

Prompt building, per-sender unsubscribe detection and the
``run_triage_agent`` driver.  The ``pydantic_ai`` /
LLM-provider imports stay lazy inside the agent functions to
keep module-load time low.
"""

from __future__ import annotations

import json
import sqlite3

from robotsix_llmio.core import Tier, run_agent

from robotsix_auto_mail._constants import _ARCHIVE_TAXONOMY_GUIDANCE
from robotsix_auto_mail.config import (
    ConfigurationError,
    resolve_llm_api_key,
    resolve_llm_provider,
)
from robotsix_auto_mail.db import (
    MailRecord,
    get_record_by_message_id,
    get_watermark,
    list_untriaged_records,
    set_watermark,
)
from robotsix_auto_mail.format import _BODY_PREVIEW_LIMIT, _effective_body_plain
from robotsix_auto_mail.triage._constants import (
    _AGENT_SELECTABLE_ACTIONS,
    _UNSUBSCRIBE_SUGGESTIONS_KEY,
)
from robotsix_auto_mail.triage.classifier import (
    _build_memory_guidance,
    _domain_key,
    _load_archive_folder_memory,
    _load_llm_archive_hints,
    _save_llm_archive_hints,
    _sender_key,
    normalize_archive_subfolder,
    propose_archive_subfolder_llm,
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
    archive_folder_history: list[str] | None = None,
    archive_folder_usage: dict[str, int] | None = None,
    user_email: str | None = None,
) -> str:
    """Build the LLM system prompt describing the triage task and actions.

    When *archive_folders* is a non-empty list, appends a paragraph
    describing the available archive folders and the optional
    ``archive_subfolder`` field.  When *archive_folder_history* is a
    non-empty list of guidance lines (one per sender/domain with a recorded
    archive-folder choice), the ``TO_ARCHIVE`` paragraph additionally lists
    those previously-used folders and instructs the model to prefer reusing
    an existing project folder for ongoing projects.  When
    *archive_folder_usage* maps folder paths to aggregated usage counts,
    each listed folder with a positive count is annotated with ``(used Nx)``
    and the model is told to prefer high-count folders.  When *user_email* is
    a non-empty string, appends an instruction telling the model never to
    classify mail the user sent to themself as ``TO_ANSWER``.
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
        if archive_folder_usage:
            folder_lines = "\n".join(
                f"- {f} (used {archive_folder_usage[f]}x)"
                if archive_folder_usage.get(f, 0) > 0
                else f"- {f}"
                for f in archive_folders
            )
        else:
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
            "suggestion.\n" + _ARCHIVE_TAXONOMY_GUIDANCE + "\n"
        )
        if archive_folder_usage:
            prompt += (
                "Prefer folders that already contain many mails (higher "
                "`used Nx` counts) over inventing a near-duplicate folder.\n"
            )
        if archive_folder_history:
            history_lines = "\n".join(archive_folder_history)
            prompt += (
                "\nArchive-folder history for senders in this batch:\n"
                f"{history_lines}\n"
                "Prefer reusing an existing project folder when the message "
                "relates to an ongoing project, rather than inventing a new "
                "folder or a date bucket.\n"
            )
    if user_email:
        prompt += (
            f"\n\nThe user's own email address is `{user_email}`. Messages "
            "whose sender IS this address were sent by the user themself — "
            "never classify them as `TO_ANSWER` (you must not reply to "
            "yourself). Classify such self-sent messages as `TO_ARCHIVE` "
            "(or `HUMAN_TRIAGE` if genuinely unsure)."
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
            f"{_body_preview(_effective_body_plain(record))}"
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
    resolved_key = resolve_llm_api_key(raise_on_missing=False)
    if not resolved_key:
        return None

    # Resolve provider.
    resolved_provider = resolve_llm_provider()

    from pydantic_ai import PromptedOutput
    from robotsix_llmio.core import get_provider

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
        "- confidence: 'low', 'medium', or 'high'\n"
        "\n"
        "Return ONLY a JSON object matching the schema — no explanation, "
        "no markdown fences."
    )

    user_message = (
        f"Sender: {recent.sender}\n"
        f"Subject: {recent.subject}\n\n"
        f"Body:\n{recent.body_plain}"
    )

    llm_provider = get_provider(provider=resolved_provider, api_key=resolved_key)
    agent_handle = llm_provider.build_agent(
        level=1,
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
        except json.JSONDecodeError, TypeError:
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


def _is_non_semantic_subfolder(subfolder: str) -> bool:
    """Return ``True`` when *subfolder*'s top-level segment looks like a domain.

    A path whose segment before the first ``/`` contains a ``.`` (e.g.
    ``lwn.net/lwn``, ``ls2n.fr/armada``) echoes sender identity rather than a
    semantic topic.  A single-segment path or one whose top segment has no
    dot (e.g. ``Newsletters/LWN``, ``Projects/armada``, ``Finance``) is
    semantic.
    """
    top = subfolder.split("/", 1)[0]
    return "." in top


def _load_archive_guidance(
    conn: sqlite3.Connection,
    remaining: list[MailRecord],
) -> tuple[list[str] | None, list[str], dict[str, int]]:
    """Load archive folders, per-sender/domain history hints, and usage counts."""
    archive_raw = get_watermark(conn, "archive_structure")
    archive_folders: list[str] | None = None
    if archive_raw is not None:
        try:
            data = json.loads(archive_raw)
            if isinstance(data, list):
                archive_folders = data
            else:
                archive_folders = data["folders"]
        except json.JSONDecodeError, TypeError, KeyError:
            archive_folders = None

    if archive_folders is not None:
        archive_folders = [
            f for f in (normalize_archive_subfolder(f) for f in archive_folders)
            if f
        ]

    folder_memory = _load_archive_folder_memory(conn)
    archive_folder_usage: dict[str, int] = {}
    for entry in folder_memory.values():
        archive_folder_usage[entry.subfolder] = (
            archive_folder_usage.get(entry.subfolder, 0) + entry.count
        )
    weak_hint = (
        " (previously used, but prefer a semantic topical folder over this "
        "domain/sender path)"
    )
    archive_folder_history: list[str] = []
    if folder_memory:
        seen_keys: set[str] = set()
        for record in remaining:
            sender_key = _sender_key(record.sender)
            sender_entry = folder_memory.get(sender_key)
            if sender_entry is not None and sender_key not in seen_keys:
                seen_keys.add(sender_key)
                line = (
                    f"- Mail from `{sender_key}` was previously archived to "
                    f"`{sender_entry.subfolder}`."
                )
                if _is_non_semantic_subfolder(sender_entry.subfolder):
                    line += weak_hint
                archive_folder_history.append(line)
            domain = _domain_key(record.sender)
            domain_entry = folder_memory.get(domain) if domain else None
            if domain_entry is not None and domain not in seen_keys:
                seen_keys.add(domain)
                times = "time" if domain_entry.count == 1 else "times"
                line = (
                    f"- Other mail from senders at domain `{domain}` was "
                    f"previously archived to `{domain_entry.subfolder}` "
                    f"({domain_entry.count} {times})."
                )
                if _is_non_semantic_subfolder(domain_entry.subfolder):
                    line += weak_hint
                archive_folder_history.append(line)
    return archive_folders, archive_folder_history, archive_folder_usage


def _persist_llm_triage_results(
    conn: sqlite3.Connection,
    remaining: list[MailRecord],
    by_index: dict[int, TriageItem],
) -> list[TriageDecision]:
    """Map LLM items back to records, clamp/default actions, and persist them."""
    decisions: list[TriageDecision] = []
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
    return decisions


def _update_archive_hints(
    conn: sqlite3.Connection,
    remaining: list[MailRecord],
    by_index: dict[int, TriageItem],
) -> None:
    """Clear stale TO_ARCHIVE subfolder hints and upsert new ones."""
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
            sub = normalize_archive_subfolder(matched.archive_subfolder or "")
            if sub:
                hints[record.message_id] = sub
    _save_llm_archive_hints(conn, hints)


def _fill_missing_archive_hints(
    conn: sqlite3.Connection,
    remaining: list[MailRecord],
    by_index: dict[int, TriageItem],
    api_key: str,
    provider: str | None,
) -> None:
    """Propose a subfolder for TO_ARCHIVE records the classifier left blank.

    The triage LLM may classify a message ``TO_ARCHIVE`` without volunteering
    an ``archive_subfolder`` (the field is optional and the cheap model often
    omits it).  Filling those gaps here — during the background triage run —
    means the board can render each destination from a cached hint instead of
    issuing an LLM call per card on every page load.  Best-effort: a missing
    key or proposal failure leaves the hint unset (the board falls back to the
    archive root), never aborting triage.
    """
    if not api_key:
        return
    hinted = set(_load_llm_archive_hints(conn))
    for i, record in enumerate(remaining, start=1):
        matched = by_index.get(i)
        if matched is None or matched.action != "TO_ARCHIVE":
            continue
        if record.message_id in hinted:
            continue
        # Persists the hint itself; swallows its own errors.
        propose_archive_subfolder_llm(conn, record, api_key, provider)


def run_triage_agent(
    conn: sqlite3.Connection,
    *,
    api_key: str | None = None,
    provider: str | None = None,
    tier: Tier = Tier.CHEAP,
    only_undecided: bool = False,
    user_email: str | None = None,
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
        provider: LLM backend name (e.g. ``openrouter-deepseek``).
            Resolves with the precedence ``provider`` argument →
            ``LLM_PROVIDER`` env var → ``config.llm_provider`` (via
            :func:`load_llm_provider`).
        tier: LLM tier to use.  ``Tier.CHEAP`` (default).
        only_undecided: When ``True``, inbox records that already have a
            ``triage_decisions`` row (per :func:`get_triage_decision`) are
            dropped before both the deterministic-rule fast-path and the
            LLM call, making the pass incremental and idempotent.  When the
            filtered set is empty, returns ``[]`` without building the LLM
            agent (no API key required).  Defaults to ``False`` (re-triage
            every inbox record, preserving the manual CLI behavior).
        user_email: The user's own email address (typically
            ``config.username``).  When non-empty, the system prompt
            instructs the model never to classify self-sent mail (sender ==
            this address) as ``TO_ANSWER``.  Defaults to ``None``.

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

    decisions: list[TriageDecision] = []
    remaining = records

    # -- resolve API key (arg -> LLM_API_KEY env -> config.llm_api_key) --
    try:
        resolved_key = resolve_llm_api_key(api_key)
    except ConfigurationError as exc:
        raise TriageError(str(exc)) from exc

    # -- resolve provider (arg -> LLM_PROVIDER env -> config.llm_provider) --
    resolved_provider = resolve_llm_provider(provider)

    # -- read archive structure + per-sender/domain history for the prompt --
    archive_folders, archive_folder_history, archive_folder_usage = (
        _load_archive_guidance(conn, remaining)
    )

    # -- lazy imports so the rest of the CLI works without pydantic_ai --
    from pydantic_ai import PromptedOutput
    from robotsix_llmio.core import get_provider

    # -- build agent --
    llm_provider = get_provider(provider=resolved_provider, api_key=resolved_key)
    agent_handle = llm_provider.build_agent(
        level=1 if tier == Tier.CHEAP else 2,
        system_prompt=_build_triage_system_prompt(
            archive_folders,
            archive_folder_history or None,
            archive_folder_usage or None,
            user_email,
        ),
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

    decisions.extend(_persist_llm_triage_results(conn, remaining, by_index))

    # -- store/clear LLM archive subfolder hints -------------------------
    _update_archive_hints(conn, remaining, by_index)
    # Fill subfolders the classifier omitted, so the board renders each
    # TO_ARCHIVE destination from a cached hint (no LLM call per render).
    _fill_missing_archive_hints(
        conn, remaining, by_index, resolved_key, resolved_provider
    )

    # -- check TO_DELETE senders for unsubscribe options ------------------
    _check_unsubscribe_for_to_delete(conn)

    return decisions
