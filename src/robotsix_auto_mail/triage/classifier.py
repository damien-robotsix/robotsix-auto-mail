"""Deterministic triage classification.

Archive-subfolder proposals (deterministic + override storage + optional
LLM hints), persistent human-decision memory, and deterministic triage
rule proposal / dedup / application.  The ``pydantic_ai`` /
``OpenRouterDeepseekProvider`` imports stay lazy inside
``propose_archive_subfolder_llm`` to keep module-load time low.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime
from email.utils import parseaddr

from robotsix_llmio.core import Tier, run_agent

from robotsix_auto_mail.db import (
    VALID_TRIAGE_ACTIONS,
    MailRecord,
    get_record_by_message_id,
    get_watermark,
    set_watermark,
)
from robotsix_auto_mail.triage._constants import (
    _ARCHIVE_LLM_HINTS_WATERMARK_KEY,
    _ARCHIVE_OVERRIDES_WATERMARK_KEY,
    _MEMORY_WATERMARK_KEY,
    _RULE_ACTIVE_WATERMARK_KEY,
    _RULE_LEDGER_WATERMARK_KEY,
    _RULE_MIN_DECISIONS,
    _VALID_RULE_STATES,
)
from robotsix_auto_mail.triage.persistence import (
    ArchiveSubfolderProposal,
    RuleLedgerEntry,
    SenderMemory,
    TriageError,
    TriageRule,
    TriageRuleProposal,
    _utc_now_iso,
    list_triage_decisions,
)

# ---------------------------------------------------------------------------
# Archive subfolder proposal — deterministic + override storage
# ---------------------------------------------------------------------------


def propose_archive_subfolder(record: MailRecord) -> str:
    """Derive a sensible archive subfolder path for *record*.

    Priority-ordered rules (first match wins; result is lowercased with
    runs of non-alphanumeric chars collapsed to ``-``, leading/trailing
    ``-`` stripped):

    1. **Mailing-list prefix** — subject starts with a bracketed token
       like ``[python-dev]`` or ``[list-name:123]`` → ``Lists/<name>``.
    2. **Sender domain + local-part** — ``example.com/alice``.
    3. **Date fallback** — ``YYYY/MM`` from ``record.date``.
    4. **All-rules-fail** — returns ``""`` (archive into root).

    No LLM involved — purely deterministic.
    """
    # -- 1. Mailing-list prefix --------------------------------------------
    m = re.match(r"^\s*\[([^\]]*?)(?:\s+\d+)?\]", record.subject)
    if m:
        name = m.group(1).strip()
        if name:
            # Strip trailing colon + digits (e.g. "[list:123]" → "list")
            name = re.sub(r":\s*\d+$", "", name).strip()
            if name:
                sanitised = _sanitise_subfolder(name)
                if sanitised:
                    return f"Lists/{sanitised}"

    # -- 2. Sender domain + local-part ------------------------------------
    sender = record.sender
    addr = parseaddr(sender)[1]
    if addr and "@" in addr:
        local_part, domain = addr.split("@", 1)
        local = _sanitise_subfolder(local_part.lower())
        dom = _sanitise_subfolder(domain.lower())
        if dom and local:
            return f"{dom}/{local}"

    # -- 3. Date fallback --------------------------------------------------
    date_str = record.date.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            dt = datetime.strptime(date_str, fmt)
            return f"{dt.year:04d}/{dt.month:02d}"
        except ValueError:
            continue
    # Try ISO-8601 via fromisoformat (Python 3.11+ handles more variants).
    try:
        dt = datetime.fromisoformat(date_str)
        return f"{dt.year:04d}/{dt.month:02d}"
    except (ValueError, TypeError):
        pass
    return "unknown"


def _sanitise_subfolder(raw: str) -> str:
    """Lowercase *raw*, collapse non-alphanumeric runs to ``-``, strip edges."""
    lowered = raw.lower()
    # Replace runs of non-alphanumeric chars with a single '-'
    sanitised = re.sub(r"[^a-z0-9]+", "-", lowered)
    sanitised = sanitised.strip("-")
    return sanitised


def _load_archive_overrides(conn: sqlite3.Connection) -> dict[str, str]:
    """Load user overrides from the watermark table.

    Returns ``{message_id: subfolder}``, empty dict on first use.
    """
    raw = get_watermark(conn, _ARCHIVE_OVERRIDES_WATERMARK_KEY)
    if raw is None:
        return {}
    data: dict[str, object] = json.loads(raw)
    return {k: str(v) for k, v in data.items()}


def _save_archive_overrides(
    conn: sqlite3.Connection, overrides: dict[str, str]
) -> None:
    """Persist *overrides* to the watermark table (json round-trip)."""
    set_watermark(conn, _ARCHIVE_OVERRIDES_WATERMARK_KEY, json.dumps(overrides))


def _load_llm_archive_hints(conn: sqlite3.Connection) -> dict[str, str]:
    """Load LLM archive subfolder hints from the watermark table.

    Returns ``{message_id: subfolder}``, empty dict on first use.
    """
    raw = get_watermark(conn, _ARCHIVE_LLM_HINTS_WATERMARK_KEY)
    if raw is None:
        return {}
    data: dict[str, object] = json.loads(raw)
    return {k: str(v) for k, v in data.items()}


def _save_llm_archive_hints(conn: sqlite3.Connection, hints: dict[str, str]) -> None:
    """Persist *hints* to the watermark table (json round-trip)."""
    set_watermark(conn, _ARCHIVE_LLM_HINTS_WATERMARK_KEY, json.dumps(hints))


def get_archive_subfolder(
    conn: sqlite3.Connection, message_id: str, record: MailRecord
) -> str:
    """Return the effective archive subfolder for *message_id*.

    Priority chain:
    1. User override (from ``archive_subfolder_overrides`` watermark).
    2. LLM hint (from ``archive_subfolder_llm_hints`` watermark).
    3. Deterministic proposal via :func:`propose_archive_subfolder`.
    """
    # 1. User override
    overrides = _load_archive_overrides(conn)
    if message_id in overrides:
        return overrides[message_id]

    # 2. LLM hint
    hints = _load_llm_archive_hints(conn)
    if message_id in hints:
        return hints[message_id]

    # 3. Deterministic proposal
    return propose_archive_subfolder(record)


def set_archive_subfolder_override(
    conn: sqlite3.Connection, message_id: str, subfolder: str
) -> None:
    """Upsert a user override for *message_id* → *subfolder*.

    An empty *subfolder* removes the override, falling back to the
    priority chain on the next :func:`get_archive_subfolder` call.
    """
    overrides = _load_archive_overrides(conn)
    if subfolder == "":
        overrides.pop(message_id, None)
    else:
        overrides[message_id] = subfolder
    _save_archive_overrides(conn, overrides)


def propose_archive_subfolder_llm(
    conn: sqlite3.Connection,
    record: MailRecord,
    api_key: str,
) -> None:
    """Run a cheap LLM to propose an archive subfolder for *record*
    and persist the hint.  Best-effort — failures are silently
    swallowed so the board falls back to the deterministic proposal.
    """
    # -- resolve API key --
    resolved_key = api_key or os.environ.get("LLM_API_KEY", "")
    if not resolved_key:
        return  # No API key → silently return

    # -- load existing archive folders from watermark --
    archive_raw = get_watermark(conn, "archive_structure")
    existing_folders: list[str] = []
    if archive_raw is not None:
        try:
            data = json.loads(archive_raw)
            if isinstance(data, list):
                existing_folders = data
            else:
                existing_folders = data["folders"]
        except (json.JSONDecodeError, TypeError, KeyError):
            existing_folders = []

    # -- load sender memory for this sender --
    memory = _load_memory(conn)
    sender_memory_entry = memory.get(_sender_key(record.sender))

    # -- build system prompt --
    system_prompt = (
        "You are an assistant that proposes archive subfolders for email.\n"
        "Given a mail message, pick the best existing archive folder for it, "
        "or propose a new folder name if none of the existing ones fit.\n"
        "Return ONLY a JSON object with a single `subfolder` field — no "
        "explanation, no markdown fences.\n"
    )
    if existing_folders:
        folder_lines = "\n".join(f"- {f}" for f in existing_folders)
        system_prompt += (
            "\nExisting archive folders:\n"
            f"{folder_lines}\n"
            "You may pick one of these or propose a new one.\n"
        )
    if sender_memory_entry is not None:
        system_prompt += (
            f"\nSender guidance: mail from {record.sender} was previously "
            f"triaged as `{sender_memory_entry.action}` "
            f"({sender_memory_entry.count} times). "
            "Use this to inform your folder proposal.\n"
        )

    # -- build user message --
    body_snippet = record.body_plain[:1000] if record.body_plain else ""
    user_message = (
        f"Sender: {record.sender}\n"
        f"Subject: {record.subject}\n"
        f"Body (first 1000 chars):\n{body_snippet}"
    )

    # -- lazy imports so the rest of the CLI works without pydantic_ai --
    from pydantic_ai import PromptedOutput
    from robotsix_llmio.openrouter_deepseek import OpenRouterDeepseekProvider

    try:
        llm_provider = OpenRouterDeepseekProvider(api_key=resolved_key)
        agent_handle = llm_provider.build_agent(
            tier=Tier.CHEAP,
            system_prompt=system_prompt,
            output_type=PromptedOutput(ArchiveSubfolderProposal),
        )

        try:
            result = run_agent(
                agent_handle,
                lambda: agent_handle.run_sync(user_message),
                label="archive subfolder proposal",
                what="archive subfolder proposal",
                trace_input=user_message,
            )
        except Exception:
            return  # LLM call failed → silently return

        proposed: ArchiveSubfolderProposal = result.output
        subfolder = proposed.subfolder.strip()
        if not subfolder:
            return  # Empty proposal → don't pollute the watermark

        # -- persist the hint --
        hints = _load_llm_archive_hints(conn)
        hints[record.message_id] = subfolder
        _save_llm_archive_hints(conn, hints)
    except Exception:
        # Any failure (import error, pydantic validation, etc.) → silently return
        return


# ---------------------------------------------------------------------------
# Human-decision memory ledger — watermark table
# ---------------------------------------------------------------------------


def _sender_key(sender: str) -> str:
    """Return the generalization key for *sender*.

    Extracts the bare email address (lowercased); falls back to the raw
    lowercased sender string when no address can be parsed.
    """
    address = parseaddr(sender)[1]
    return (address or sender).strip().lower()


def _domain_key(sender: str) -> str:
    """Return the lowercased domain of *sender*, or ``""`` when absent."""
    key = _sender_key(sender)
    if "@" in key:
        return key.split("@", 1)[1]
    return ""


def _load_memory(conn: sqlite3.Connection) -> dict[str, SenderMemory]:
    """Load the human-decision memory from the watermark table.

    Returns an empty dict when the memory has never been written.
    """
    raw = get_watermark(conn, _MEMORY_WATERMARK_KEY)
    if raw is None:
        return {}
    data: dict[str, object] = json.loads(raw)
    return {
        sender: SenderMemory.model_validate(entry) for sender, entry in data.items()
    }


def _save_memory(conn: sqlite3.Connection, memory: dict[str, SenderMemory]) -> None:
    """Persist *memory* to the watermark table (json round-trip)."""
    payload = {sender: entry.model_dump() for sender, entry in memory.items()}
    set_watermark(conn, _MEMORY_WATERMARK_KEY, json.dumps(payload))


def record_human_decision(
    conn: sqlite3.Connection, message_id: str, action: str
) -> None:
    """Remember a human triage *action* for the sender of *message_id*.

    Looks up the sender via :func:`get_record_by_message_id`, updates that
    sender's :class:`SenderMemory` entry (incrementing ``count`` and moving
    ``action`` toward the latest human decision) and persists the memory.
    A no-op when *message_id* is unknown.  Validates *action* against
    :data:`VALID_TRIAGE_ACTIONS`.
    """
    if action not in VALID_TRIAGE_ACTIONS:
        raise TriageError(
            f"action must be one of {sorted(VALID_TRIAGE_ACTIONS)!r}; got {action!r}"
        )
    record = get_record_by_message_id(conn, message_id)
    if record is None:
        return
    key = _sender_key(record.sender)
    memory = _load_memory(conn)
    previous = memory.get(key)
    if previous is None:
        entry = SenderMemory(
            action=action,
            count=1,
            last_action=action,
            updated_at=_utc_now_iso(),
        )
    else:
        entry = SenderMemory(
            action=action,
            count=previous.count + 1,
            last_action=previous.action,
            updated_at=_utc_now_iso(),
        )
    memory[key] = entry
    _save_memory(conn, memory)


def _build_memory_guidance(conn: sqlite3.Connection) -> str:
    """Render the human-decision memory as concise prompt guidance.

    Returns one line per remembered sender (ordered by sender key) and an
    empty string when the memory is empty.
    """
    memory = _load_memory(conn)
    if not memory:
        return ""
    lines = [
        "Established human triage preferences (advisory — follow unless "
        "the new message clearly differs):"
    ]
    for sender in sorted(memory):
        entry = memory[sender]
        times = "time" if entry.count == 1 else "times"
        lines.append(
            f"- Mail from `{sender}` was triaged by the user as "
            f"`{entry.action}` ({entry.count} {times}) — prefer this "
            "unless the new message clearly differs."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deterministic triage rules — proposal, dedup ledger and application
# ---------------------------------------------------------------------------


def _rule_fingerprint(rule: TriageRule) -> str:
    """Return a deterministic fingerprint identifying *rule*.

    Derived from the stable identity triple ``(match_type, match_value,
    action)`` — each stripped and lower-cased — and hashed with SHA-256.
    Presentation fields (``title`` / ``body`` / ``confidence``) are
    deliberately EXCLUDED so a reworded proposal keeps the same identity,
    mirroring :func:`config_sync_agent._proposal_fingerprint`.
    """
    raw = (
        f"{rule.match_type.strip().lower()}"
        "\x00"
        f"{rule.match_value.strip().lower()}"
        "\x00"
        f"{rule.action.strip().lower()}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _confidence_for(count: int) -> str:
    """Map an evidence *count* to a confidence level."""
    if count >= 6:
        return "high"
    if count >= 4:
        return "medium"
    return "low"


def _load_rule_ledger(conn: sqlite3.Connection) -> dict[str, RuleLedgerEntry]:
    """Load the rule dedup ledger from the watermark table."""
    raw = get_watermark(conn, _RULE_LEDGER_WATERMARK_KEY)
    if raw is None:
        return {}
    data: dict[str, object] = json.loads(raw)
    return {
        fingerprint: RuleLedgerEntry.model_validate(entry)
        for fingerprint, entry in data.items()
    }


def _save_rule_ledger(
    conn: sqlite3.Connection, ledger: dict[str, RuleLedgerEntry]
) -> None:
    """Persist *ledger* to the watermark table (json round-trip)."""
    payload = {fingerprint: entry.model_dump() for fingerprint, entry in ledger.items()}
    set_watermark(conn, _RULE_LEDGER_WATERMARK_KEY, json.dumps(payload))


def _load_active_rules(conn: sqlite3.Connection) -> list[TriageRule]:
    """Load the accepted (active) deterministic rules from the watermark."""
    raw = get_watermark(conn, _RULE_ACTIVE_WATERMARK_KEY)
    if raw is None:
        return []
    data: list[object] = json.loads(raw)
    return [TriageRule.model_validate(entry) for entry in data]


def _save_active_rules(conn: sqlite3.Connection, rules: list[TriageRule]) -> None:
    """Persist the active-rules list to the watermark table."""
    payload = [rule.model_dump() for rule in rules]
    set_watermark(conn, _RULE_ACTIVE_WATERMARK_KEY, json.dumps(payload))


def propose_triage_rules(
    conn: sqlite3.Connection,
) -> list[TriageRuleProposal]:
    """Derive candidate deterministic triage rules from triage history.

    Scans :func:`list_triage_decisions`, resolving each decision's sender
    via :func:`get_record_by_message_id`.  ``HUMAN_TRIAGE`` decisions and
    decisions whose mail is no longer present are ignored.  A *sender* rule
    is proposed when a single sender maps consistently to one non-
    ``HUMAN_TRIAGE`` action across at least :data:`_RULE_MIN_DECISIONS`
    decisions; a *domain* rule is proposed when at least two distinct senders
    in a domain all map to the same single action across at least
    :data:`_RULE_MIN_DECISIONS` decisions in total.  No LLM is involved.

    Proposals are returned sorted by ``(match_type, match_value, action)``
    for deterministic output; the caller is responsible for dedup via
    :func:`record_and_filter_rule_proposals`.
    """
    # sender_key -> list of non-HUMAN_TRIAGE actions
    by_sender: dict[str, list[str]] = {}
    # domain -> {sender_key -> list of non-HUMAN_TRIAGE actions}
    by_domain: dict[str, dict[str, list[str]]] = {}

    for decision in list_triage_decisions(conn):
        if decision.action == "HUMAN_TRIAGE":
            continue
        record = get_record_by_message_id(conn, decision.message_id)
        if record is None:
            continue
        sender = _sender_key(record.sender)
        by_sender.setdefault(sender, []).append(decision.action)
        domain = _domain_key(record.sender)
        if domain:
            by_domain.setdefault(domain, {}).setdefault(sender, []).append(
                decision.action
            )

    proposals: list[TriageRuleProposal] = []

    # -- sender rules --
    for sender, actions in by_sender.items():
        distinct = set(actions)
        if len(distinct) != 1 or len(actions) < _RULE_MIN_DECISIONS:
            continue
        action = next(iter(distinct))
        count = len(actions)
        proposals.append(
            TriageRuleProposal(
                match_type="sender",
                match_value=sender,
                action=action,
                title=f"Triage mail from {sender} as {action}",
                body=(
                    f"All {count} triaged messages from `{sender}` were "
                    f"classified as `{action}`. Propose a deterministic rule "
                    f"so future mail from this sender is triaged as "
                    f"`{action}` without an LLM call."
                ),
                confidence=_confidence_for(count),
            )
        )

    # -- domain rules --
    for domain, senders in by_domain.items():
        if len(senders) < 2:
            continue
        all_actions = [a for actions in senders.values() for a in actions]
        distinct = set(all_actions)
        if len(distinct) != 1 or len(all_actions) < _RULE_MIN_DECISIONS:
            continue
        action = next(iter(distinct))
        count = len(all_actions)
        proposals.append(
            TriageRuleProposal(
                match_type="domain",
                match_value=domain,
                action=action,
                title=f"Triage mail from domain {domain} as {action}",
                body=(
                    f"{len(senders)} distinct senders in domain `{domain}` "
                    f"accounted for {count} triaged messages, all classified "
                    f"as `{action}`. Propose a deterministic rule so future "
                    f"mail from this domain is triaged as `{action}` without "
                    f"an LLM call."
                ),
                confidence=_confidence_for(count),
            )
        )

    proposals.sort(key=lambda p: (p.match_type, p.match_value, p.action))
    return proposals


def record_and_filter_rule_proposals(
    conn: sqlite3.Connection, proposals: list[TriageRuleProposal]
) -> list[TriageRuleProposal]:
    """Record genuinely-new rule proposals and filter already-seen ones.

    A proposal is *new* iff its fingerprint is absent from the ledger in
    ANY state — ``pending`` / ``accepted`` / ``rejected`` all suppress
    re-proposal.  New proposals are recorded as ``pending`` and returned in
    input order; the ledger is only written when there is something new,
    mirroring :func:`config_sync_agent.record_and_filter_proposals`.
    """
    ledger = _load_rule_ledger(conn)
    new_proposals: list[TriageRuleProposal] = []
    for proposal in proposals:
        fingerprint = _rule_fingerprint(proposal)
        if fingerprint in ledger:
            continue
        ledger[fingerprint] = RuleLedgerEntry(
            match_type=proposal.match_type,
            match_value=proposal.match_value,
            action=proposal.action,
            title=proposal.title,
            state="pending",
        )
        new_proposals.append(proposal)
    if new_proposals:
        _save_rule_ledger(conn, ledger)
    return new_proposals


def set_rule_state(conn: sqlite3.Connection, fingerprint: str, state: str) -> None:
    """Transition the ledger entry *fingerprint* to *state*.

    Accepting (``state == "accepted"``) adds the underlying
    :class:`TriageRule` to the active-rules list; any other state removes it
    (so rejecting never leaves an active rule behind).  Raises
    :class:`TriageError` for an invalid *state* or an unknown *fingerprint*.
    """
    if state not in _VALID_RULE_STATES:
        raise TriageError(
            f"state must be one of {sorted(_VALID_RULE_STATES)!r}; got {state!r}"
        )
    ledger = _load_rule_ledger(conn)
    entry = ledger.get(fingerprint)
    if entry is None:
        raise TriageError(f"No triage rule proposal with fingerprint {fingerprint!r}")
    ledger[fingerprint] = entry.model_copy(update={"state": state})
    _save_rule_ledger(conn, ledger)

    rule = TriageRule(
        match_type=entry.match_type,
        match_value=entry.match_value,
        action=entry.action,
    )
    active = _load_active_rules(conn)
    present = any(_rule_fingerprint(r) == fingerprint for r in active)
    if state == "accepted":
        if not present:
            active.append(rule)
            _save_active_rules(conn, active)
    elif present:
        active = [r for r in active if _rule_fingerprint(r) != fingerprint]
        _save_active_rules(conn, active)


def list_rule_proposals(
    conn: sqlite3.Connection, state: str = "pending"
) -> list[tuple[str, RuleLedgerEntry]]:
    """Return (fingerprint, entry) pairs from the rule ledger whose
    ``state`` matches *state*, sorted deterministically by
    ``(match_type, match_value, action)``.
    """
    if state not in _VALID_RULE_STATES:
        raise TriageError(
            f"state must be one of {sorted(_VALID_RULE_STATES)!r}; got {state!r}"
        )
    ledger = _load_rule_ledger(conn)
    pairs = [
        (fingerprint, entry)
        for fingerprint, entry in ledger.items()
        if entry.state == state
    ]
    pairs.sort(
        key=lambda item: (
            item[1].match_type,
            item[1].match_value,
            item[1].action,
        )
    )
    return pairs


def _rule_matches(rule: TriageRule, record: MailRecord) -> bool:
    """Return whether *record* matches *rule*."""
    value = rule.match_value.strip().lower()
    if rule.match_type == "sender":
        return _sender_key(record.sender) == value
    if rule.match_type == "domain":
        return _domain_key(record.sender) == value
    if rule.match_type == "subject_contains":
        return value in record.subject.lower()
    return False


def apply_triage_rules(conn: sqlite3.Connection, record: MailRecord) -> str | None:
    """Return the action of the first active rule matching *record*.

    Matches by exact lowercased sender, sender domain, or case-insensitive
    subject substring (in active-rule order).  Returns ``None`` when no
    active rule matches.
    """
    for rule in _load_active_rules(conn):
        if _rule_matches(rule, record):
            return rule.action
    return None
