"""Deterministic triage classification.

Archive-subfolder proposals (deterministic + override storage + optional
LLM hints), persistent human-decision memory, and deterministic triage
rule proposal / dedup / application.  The ``pydantic_ai`` /
LLM-provider imports stay lazy inside
``propose_archive_subfolder_llm`` to keep module-load time low.
"""

from __future__ import annotations

import json
import re
import sqlite3
from email.utils import parseaddr
from typing import cast

from robotsix_llmio.core import run_agent

from robotsix_auto_mail._constants import _ARCHIVE_TAXONOMY_GUIDANCE
from robotsix_auto_mail.config import (
    resolve_llm_api_key,
    resolve_llm_provider,
)
from robotsix_auto_mail.db import (
    VALID_TRIAGE_ACTIONS,
    MailRecord,
    get_record_by_message_id,
    get_watermark,
    set_watermark,
)
from robotsix_auto_mail.triage._constants import (
    _ARCHIVE_FOLDER_MEMORY_WATERMARK_KEY,
    _ARCHIVE_LLM_HINTS_WATERMARK_KEY,
    _ARCHIVE_OVERRIDES_WATERMARK_KEY,
    _MEMORY_WATERMARK_KEY,
)
from robotsix_auto_mail.triage.persistence import (
    ArchiveFolderMemory,
    ArchiveSubfolderProposal,
    SenderMemory,
    TriageError,
    _utc_now_iso,
)

# ---------------------------------------------------------------------------
# Archive subfolder proposal — deterministic + override storage
# ---------------------------------------------------------------------------

#: A leading path segment the LLM tends to echo back: the archive root itself
#: (``robotsix-mail-archive``) or a near-miss typo of it.  Stripped so a
#: proposal never double-prefixes when joined under the archive root.
_ARCHIVE_ROOT_SEGMENT_RE = re.compile(r".*mail-archive$", re.IGNORECASE)


def normalize_archive_subfolder(subfolder: str) -> str:
    """Normalise an archive-subfolder proposal to a clean RELATIVE path.

    Guards against two LLM misbehaviours seen in real proposals:

    * **echoing the archive root** (``robotsix-mail-archive/Billing``, or a
      typo like ``robosix-mail-archive/Billing``) — a leading root-like
      segment is dropped so callers that join the result under the archive
      root do not produce ``robotsix-mail-archive/robotsix-mail-archive/…``;
    * **returning a triage action** (``TO_DELETE``, ``TO_ARCHIVE`` …) as a
      folder name — any such segment is dropped, so mail is never filed into
      a triage-action-named folder.

    Returns a ``/``-separated relative path, or ``""`` (archive into the
    root) when nothing meaningful remains.
    """
    segments = [seg.strip() for seg in subfolder.strip().split("/")]
    cleaned: list[str] = []
    for index, seg in enumerate(segments):
        if not seg:
            continue
        if index == 0 and _ARCHIVE_ROOT_SEGMENT_RE.match(seg):
            continue
        if seg.upper() in VALID_TRIAGE_ACTIONS:
            continue
        cleaned.append(seg)
    return "/".join(cleaned)


def propose_archive_subfolder(record: MailRecord) -> str:
    """Derive a sensible archive subfolder path for *record*.

    Priority-ordered rules (first match wins; result is lowercased with
    runs of non-alphanumeric chars collapsed to ``-``, leading/trailing
    ``-`` stripped):

    1. **Mailing-list prefix** — subject starts with a bracketed token
       like ``[python-dev]`` or ``[list-name:123]`` → ``Lists/<name>``.
    2. **All-rules-fail** — returns ``""`` (archive into root).

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

    # -- 2. All-rules-fail → archive root ---------------------------------
    return ""


def _sanitise_subfolder(raw: str) -> str:
    """Lowercase *raw*, collapse non-alphanumeric runs to ``-``, strip edges."""
    lowered = raw.lower()
    # Replace runs of non-alphanumeric chars with a single '-'
    sanitised = re.sub(r"[^a-z0-9]+", "-", lowered)
    sanitised = sanitised.strip("-")
    return sanitised


def _load_json_watermark(conn: sqlite3.Connection, key: str) -> dict[str, object]:
    """Read a JSON-serialised dict watermark, returning {} when absent."""
    raw = get_watermark(conn, key)
    if raw is None:
        return {}
    return cast(dict[str, object], json.loads(raw))


def _save_json_watermark(
    conn: sqlite3.Connection, key: str, data: dict[str, object]
) -> None:
    """Persist *data* as a JSON-serialised watermark."""
    set_watermark(conn, key, json.dumps(data))


def _load_archive_overrides(conn: sqlite3.Connection) -> dict[str, str]:
    """Load user overrides from the watermark table.

    Returns ``{message_id: subfolder}``, empty dict on first use.
    """
    return {
        k: str(v)
        for k, v in _load_json_watermark(conn, _ARCHIVE_OVERRIDES_WATERMARK_KEY).items()
    }


def _save_archive_overrides(
    conn: sqlite3.Connection, overrides: dict[str, str]
) -> None:
    """Persist *overrides* to the watermark table (json round-trip)."""
    _save_json_watermark(
        conn, _ARCHIVE_OVERRIDES_WATERMARK_KEY, cast("dict[str, object]", overrides)
    )


def _load_llm_archive_hints(conn: sqlite3.Connection) -> dict[str, str]:
    """Load LLM archive subfolder hints from the watermark table.

    Returns ``{message_id: subfolder}``, empty dict on first use.
    """
    return {
        k: str(v)
        for k, v in _load_json_watermark(conn, _ARCHIVE_LLM_HINTS_WATERMARK_KEY).items()
    }


def _save_llm_archive_hints(conn: sqlite3.Connection, hints: dict[str, str]) -> None:
    """Persist *hints* to the watermark table (json round-trip)."""
    _save_json_watermark(
        conn, _ARCHIVE_LLM_HINTS_WATERMARK_KEY, cast("dict[str, object]", hints)
    )


def _load_archive_folder_memory(
    conn: sqlite3.Connection,
) -> dict[str, ArchiveFolderMemory]:
    """Load the archive-folder memory from the watermark table.

    Returns ``{key: ArchiveFolderMemory}`` keyed by sender key and sender
    domain; an empty dict when the memory has never been written.
    """
    return {
        key: ArchiveFolderMemory.model_validate(entry)
        for key, entry in _load_json_watermark(
            conn, _ARCHIVE_FOLDER_MEMORY_WATERMARK_KEY
        ).items()
    }


def _save_archive_folder_memory(
    conn: sqlite3.Connection, memory: dict[str, ArchiveFolderMemory]
) -> None:
    """Persist *memory* to the watermark table (json round-trip)."""
    payload = {key: entry.model_dump() for key, entry in memory.items()}
    _save_json_watermark(
        conn, _ARCHIVE_FOLDER_MEMORY_WATERMARK_KEY, cast("dict[str, object]", payload)
    )


def record_archive_folder_choice(
    conn: sqlite3.Connection, record: MailRecord, subfolder: str
) -> None:
    """Remember that *record*'s sender / domain mail is filed into *subfolder*.

    Upserts BOTH a sender-key entry (``_sender_key``) and a sender-domain
    entry, incrementing ``count`` on repeat so the most-used folder can be
    surfaced.  A no-op when *subfolder* is empty; the domain entry is skipped
    when the sender address has no ``@``.  Record only human-confirmed folder
    choices here — never the LLM's own proposal/hint.
    """
    if not subfolder:
        return
    keys = [_sender_key(record.sender)]
    domain = _domain_key(record.sender)
    if domain:
        keys.append(domain)
    memory = _load_archive_folder_memory(conn)
    for key in keys:
        previous = memory.get(key)
        count = previous.count + 1 if previous is not None else 1
        memory[key] = ArchiveFolderMemory(
            subfolder=subfolder,
            count=count,
            updated_at=_utc_now_iso(),
        )
    _save_archive_folder_memory(conn, memory)


def get_archive_subfolder(
    conn: sqlite3.Connection,
    message_id: str,
    record: MailRecord,
    api_key: str = "",
) -> str:
    """Return the effective archive subfolder for *message_id*.

    Priority chain:
    1. User override (from ``archive_subfolder_overrides`` watermark).
    2. LLM hint (previously persisted in ``archive_subfolder_llm_hints``).
    3. On-the-fly LLM proposal — only when *api_key* is non-empty; calls
       :func:`propose_archive_subfolder_llm` and re-reads hints so a
       successful proposal also populates the hint for next time.
    4. Deterministic proposal via :func:`propose_archive_subfolder`.
    """
    # 1. User override
    overrides = _load_archive_overrides(conn)
    if message_id in overrides:
        return normalize_archive_subfolder(overrides[message_id])

    # 2. LLM hint (previously persisted)
    hints = _load_llm_archive_hints(conn)
    if message_id in hints:
        return normalize_archive_subfolder(hints[message_id])

    # 3. On-the-fly LLM proposal (NEW)
    if api_key:
        propose_archive_subfolder_llm(conn, record, api_key)
        hints = _load_llm_archive_hints(conn)  # re-read after persist
        if message_id in hints:
            return normalize_archive_subfolder(hints[message_id])

    # 4. Deterministic fallback (stripped-down)
    return normalize_archive_subfolder(propose_archive_subfolder(record))


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
    provider: str | None = None,
) -> None:
    """Run a cheap LLM to propose an archive subfolder for *record*
    and persist the hint.  Best-effort — failures are silently
    swallowed so the board falls back to the deterministic proposal.
    """
    # -- resolve API key --
    resolved_key = resolve_llm_api_key(api_key, raise_on_missing=False)
    if not resolved_key:
        return  # No API key → silently return

    # -- resolve provider --
    resolved_provider = resolve_llm_provider(provider)

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
        except json.JSONDecodeError, TypeError, KeyError:
            existing_folders = []

    # -- load sender memory for this sender --
    memory = _load_memory(conn)
    sender_memory_entry = memory.get(_sender_key(record.sender))

    # -- load archive-folder memory for this sender / domain --
    folder_memory = _load_archive_folder_memory(conn)
    sender_folder_entry = folder_memory.get(_sender_key(record.sender))
    domain = _domain_key(record.sender)
    domain_folder_entry = folder_memory.get(domain) if domain else None

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
    if sender_folder_entry is not None or domain_folder_entry is not None:
        system_prompt += "\nArchive-folder history:\n"
        if sender_folder_entry is not None:
            system_prompt += (
                f"Mail from this sender was previously archived to "
                f"`{sender_folder_entry.subfolder}`.\n"
            )
        if domain_folder_entry is not None:
            times = "time" if domain_folder_entry.count == 1 else "times"
            system_prompt += (
                f"Other mail from senders at domain `{domain}` was previously "
                f"archived to `{domain_folder_entry.subfolder}` "
                f"({domain_folder_entry.count} {times}).\n"
            )
        system_prompt += (
            "Prefer reusing an existing project folder when the message "
            "relates to an ongoing project, rather than inventing a new "
            "folder or a date bucket.\n"
        )
    system_prompt += "\nFolder taxonomy rules:\n" + _ARCHIVE_TAXONOMY_GUIDANCE + "\n"

    # -- build user message --
    body_snippet = record.body_plain[:1000] if record.body_plain else ""
    user_message = (
        f"Sender: {record.sender}\n"
        f"Subject: {record.subject}\n"
        f"Body (first 1000 chars):\n{body_snippet}"
    )

    # -- lazy imports so the rest of the CLI works without pydantic_ai --
    from pydantic_ai import PromptedOutput
    from robotsix_llmio.core import get_provider_for_identifier

    try:
        llm_provider = get_provider_for_identifier(
            identifier=resolved_provider, api_key=resolved_key
        )
        agent_handle = llm_provider.build_agent(
            level=1,
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
        subfolder = normalize_archive_subfolder(proposed.subfolder)
        if not subfolder:
            return  # Empty / root-only / action-only proposal → don't persist

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
