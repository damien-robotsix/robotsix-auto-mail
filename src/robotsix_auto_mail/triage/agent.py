"""LLM-driven inbox triage agent.

Prompt building, per-sender unsubscribe detection and the
``run_triage_agent`` driver.  The ``pydantic_ai`` /
LLM-provider imports stay lazy inside the agent functions to
keep module-load time low.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path

from robotsix_auto_mail.config import (
    ConfigurationError,
    resolve_llm_api_key,
    resolve_llm_provider_model,
)
from robotsix_auto_mail.core._constants import _ARCHIVE_TAXONOMY_GUIDANCE
from robotsix_auto_mail.core._llm_agent import _run_llm_agent
from robotsix_auto_mail.core.format import _BODY_PREVIEW_LIMIT, _effective_body_plain
from robotsix_auto_mail.db import (
    MailRecord,
    get_record_by_message_id,
    get_watermark,
    list_untriaged_records,
    set_watermark,
)
from robotsix_auto_mail.triage._constants import (
    _AGENT_SELECTABLE_ACTIONS,
    _UNSUBSCRIBE_SUGGESTIONS_KEY,
)
from robotsix_auto_mail.triage.classifier import (
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
from robotsix_auto_mail.triage.rules import load_rules

# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def _build_triage_system_prompt(
    archive_folders: list[str] | None = None,
    user_email: str | None = None,
) -> str:
    """Build the LLM system prompt describing the triage task and actions.

    When *archive_folders* is a non-empty list, appends a paragraph
    describing the available archive folders and the optional
    ``archive_subfolder`` field.  When *user_email* is a non-empty string,
    appends an instruction telling the model never to classify mail the user
    sent to themself as ``TO_ANSWER``.  Learned per-sender/topic preferences
    are supplied separately as the ``triage_rules.md`` guidance prepended to
    the user message (see :func:`run_triage_agent`).
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
        "Common mail categories and their typical triage (apply these patterns "
        "when they fit, but always consider the specific content):\n"
        '- Newsletters / digests (e.g. "Weekly Roundup", "Monthly Digest") → '
        'TO_ARCHIVE, archive_subfolder ~ "Newsletters/<source-name>"\n'
        '- Receipts / invoices (e.g. "Your receipt from X", "Invoice #N") → '
        'TO_ARCHIVE, archive_subfolder ~ "Finance"\n'
        "- Order / shipping confirmations → TO_ARCHIVE, "
        'archive_subfolder ~ "Orders"\n'
        "- Calendar invites / event reminders → HUMAN_TRIAGE\n"
        "- Automated CI / monitoring alerts (e.g. GitHub Actions, Sentry) → "
        'TO_ARCHIVE, archive_subfolder ~ "Notifications"\n'
        '- Account / security notices (e.g. "Password changed", '
        '"New sign-in") → TO_ARCHIVE, archive_subfolder ~ "Admin"\n'
        "- Personal / direct email from a known contact → TO_ANSWER\n"
        "- Unsolicited promotional email from an unknown sender → TO_DELETE\n"
        "- Password-reset / email-verification links → TO_ANSWER (time-sensitive)\n"
        "\n"
        "Reference each message by its 1-based `index` (do NOT echo back any "
        "message id). For each message return an `index`, an `action` (one "
        "of the values above), a short `reason`, and a `confidence` of "
        "`low`, `medium`, or `high`. Prefer `HUMAN_TRIAGE` over guessing.\n"
        "\n"
        "Confidence levels:\n"
        "- `high` — the message clearly matches a well-known pattern (e.g. an\n"
        "  obvious newsletter, a receipt from a recognised vendor, unambiguous\n"
        "  spam).  You would be surprised if this classification were wrong.\n"
        "- `medium` — the message fits a general category but has some ambiguity\n"
        "  (e.g. a mailing-list post that might need a reply, a notification\n"
        "  from an unfamiliar service).  A human might reasonably disagree.\n"
        "- `low` — you are guessing.  The message does not clearly fit any\n"
        "  pattern you recognise.  Prefer `HUMAN_TRIAGE` in this case unless\n"
        "  the action is low-risk (e.g. `TO_ARCHIVE` for a clearly archival\n"
        "  message whose exact folder you are unsure about).\n"
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
            "suggestion.\n" + _ARCHIVE_TAXONOMY_GUIDANCE + "\n"
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
    resolved_provider_model = resolve_llm_provider_model()

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

    try:
        return _run_llm_agent(
            api_key=resolved_key,
            provider_model=resolved_provider_model,
            level=1,
            system_prompt=system_prompt,
            output_model=UnsubscribeDetection,
            user_message=user_message,
            label="unsubscribe detection",
            what="unsubscribe detection",
            exc_type=TriageError,
        )
    except TriageError:
        return None


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
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            # Malformed suggestions-cache JSON — keep the empty dict above.
            suggestions = json.loads(raw)

    updated = False
    for sender_key, sender_records in by_sender.items():
        if len(sender_records) < 3:
            continue
        if sender_key in suggestions:
            continue  # already cached
        detection = _detect_unsubscribe_for_sender(conn, sender_key, sender_records)
        # Only cache if an unsubscribe mechanism was actually found.
        if detection is not None and detection.has_unsubscribe:
            suggestions[sender_key] = detection.model_dump()
            updated = True

    if updated:
        set_watermark(conn, _UNSUBSCRIBE_SUGGESTIONS_KEY, json.dumps(suggestions))


def _load_archive_folders(conn: sqlite3.Connection) -> list[str] | None:
    """Load and normalise the archive folder structure for the prompt."""
    archive_raw = get_watermark(conn, "archive_structure")
    archive_folders: list[str] | None = None
    if archive_raw is not None:
        try:
            data = json.loads(archive_raw)
            archive_folders = data if isinstance(data, list) else data["folders"]
        except json.JSONDecodeError, TypeError, KeyError:
            archive_folders = None

    if archive_folders is not None:
        archive_folders = [
            f for f in (normalize_archive_subfolder(f) for f in archive_folders) if f
        ]
    return archive_folders


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
    provider_model: str | None,
    rules: str = "",
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
        propose_archive_subfolder_llm(
            conn, record, api_key, provider_model, rules=rules
        )


def run_triage_agent(
    conn: sqlite3.Connection,
    *,
    api_key: str | None = None,
    provider_model: str | None = None,
    level: int = 1,
    only_undecided: bool = False,
    user_email: str | None = None,
    rules_path: str | Path | None = None,
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
            ``config.llm_api_key`` (via
            :func:`~robotsix_auto_mail.config.resolve_llm_api_key`).
        provider_model: LLM provider-model identifier
            (e.g. ``openrouter-deepseek``).
            Resolves with the precedence ``provider_model`` argument →
            ``LLM_PROVIDER_MODEL`` env var → ``config.llm_provider_model`` (via
            :func:`~robotsix_auto_mail.config.resolve_llm_provider_model`).
        level: LLM integer tier to use.  ``1`` (cheap, default).
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

    # -- resolve provider-model (arg → LLM_PROVIDER_MODEL env →
    #    config.llm_provider_model) --
    resolved_provider_model = resolve_llm_provider_model(provider_model)

    # -- read archive folder structure for the prompt --
    archive_folders = _load_archive_folders(conn)

    system_prompt = _build_triage_system_prompt(archive_folders, user_email)

    user_message = _build_user_message(remaining)

    # -- bias the model toward the human-readable triage rules (advisory) --
    rules = load_rules(Path(rules_path)) if rules_path else ""
    if rules.strip():
        guidance = (
            "The user maintains these triage rules (advisory — follow them "
            "unless a message clearly differs):\n"
            f"{rules.strip()}"
        )
        user_message = f"{guidance}\n\n{user_message}"

    output: TriageResult = _run_llm_agent(
        api_key=resolved_key,
        provider_model=resolved_provider_model,
        level=level,
        system_prompt=system_prompt,
        output_model=TriageResult,
        user_message=user_message,
        label="mail triage",
        what="mail triage",
        exc_type=TriageError,
    )

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
        conn, remaining, by_index, resolved_key, resolved_provider_model, rules=rules
    )

    # -- check TO_DELETE senders for unsubscribe options ------------------
    _check_unsubscribe_for_to_delete(conn)

    return decisions
