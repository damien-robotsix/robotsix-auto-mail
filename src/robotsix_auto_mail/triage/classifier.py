"""Deterministic triage classification and archive-subfolder proposals.

Archive-subfolder proposals (deterministic + per-message override storage +
optional per-message LLM hints) and sender-key helpers.  Advisory guidance
for the archive-subfolder LLM proposal is passed via the ``rules`` argument
(sourced from ``MailConfig.triage_guidance``).
The LLM proposal is delegated (lazily) to the unified triage agent in
:mod:`robotsix_auto_mail.triage.agent`.
"""

from __future__ import annotations

import re
import sqlite3
from email.utils import parseaddr
from typing import cast

from robotsix_auto_mail.db import (
    VALID_TRIAGE_ACTIONS,
    MailRecord,
    load_json_watermark,
    save_json_watermark,
)
from robotsix_auto_mail.triage._constants import (
    _ARCHIVE_LLM_HINTS_WATERMARK_KEY,
    _ARCHIVE_OVERRIDES_WATERMARK_KEY,
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


def _load_archive_overrides(conn: sqlite3.Connection) -> dict[str, str]:
    """Load user overrides from the watermark table.

    Returns ``{message_id: subfolder}``, empty dict on first use.
    """
    return {
        k: str(v)
        for k, v in load_json_watermark(conn, _ARCHIVE_OVERRIDES_WATERMARK_KEY).items()
    }


def _save_archive_overrides(
    conn: sqlite3.Connection, overrides: dict[str, str]
) -> None:
    """Persist *overrides* to the watermark table (json round-trip)."""
    save_json_watermark(
        conn, _ARCHIVE_OVERRIDES_WATERMARK_KEY, cast("dict[str, object]", overrides)
    )


def _load_llm_archive_hints(conn: sqlite3.Connection) -> dict[str, str]:
    """Load LLM archive subfolder hints from the watermark table.

    Returns ``{message_id: subfolder}``, empty dict on first use.
    """
    return {
        k: str(v)
        for k, v in load_json_watermark(conn, _ARCHIVE_LLM_HINTS_WATERMARK_KEY).items()
    }


def _save_llm_archive_hints(conn: sqlite3.Connection, hints: dict[str, str]) -> None:
    """Persist *hints* to the watermark table (json round-trip)."""
    save_json_watermark(
        conn, _ARCHIVE_LLM_HINTS_WATERMARK_KEY, cast("dict[str, object]", hints)
    )


def get_archive_subfolder_with_source(
    conn: sqlite3.Connection,
    message_id: str,
    record: MailRecord,
    api_key: str = "",
    level: int = 1,
    rules: str = "",
) -> tuple[str, str]:
    """Return ``(subfolder, source)`` for *message_id*.

    The ``source`` is one of ``"override"``, ``"llm"``, or ``"rule"``,
    reflecting which tier of the priority chain produced the result.

    Priority chain:
    1. User override (from ``archive_subfolder_overrides`` watermark).
    2. LLM hint (previously persisted in ``archive_subfolder_llm_hints``).
    3. On-the-fly LLM proposal — only when *api_key* is non-empty; calls
       :func:`propose_archive_subfolder_llm` (passing *rules* for guidance)
       and re-reads hints so a successful proposal also populates the hint
       for next time.
    4. Deterministic proposal via :func:`propose_archive_subfolder`.
    """
    # 1. User override
    overrides = _load_archive_overrides(conn)
    if message_id in overrides:
        return normalize_archive_subfolder(overrides[message_id]), "override"

    # 2. LLM hint (previously persisted)
    hints = _load_llm_archive_hints(conn)
    if message_id in hints:
        return normalize_archive_subfolder(hints[message_id]), "llm"

    # 3. On-the-fly LLM proposal (NEW)
    if api_key:
        propose_archive_subfolder_llm(conn, record, api_key, level=level, rules=rules)
        hints = _load_llm_archive_hints(conn)  # re-read after persist
        if message_id in hints:
            return normalize_archive_subfolder(hints[message_id]), "llm"

    # 4. Deterministic fallback (stripped-down)
    return normalize_archive_subfolder(propose_archive_subfolder(record)), "rule"


def get_archive_subfolder(
    conn: sqlite3.Connection,
    message_id: str,
    record: MailRecord,
    api_key: str = "",
    level: int = 1,
    rules: str = "",
) -> str:
    """Return the effective archive subfolder for *message_id*.

    Priority chain:
    1. User override (from ``archive_subfolder_overrides`` watermark).
    2. LLM hint (previously persisted in ``archive_subfolder_llm_hints``).
    3. On-the-fly LLM proposal — only when *api_key* is non-empty; calls
       :func:`propose_archive_subfolder_llm` (passing *rules* for guidance)
       and re-reads hints so a successful proposal also populates the hint
       for next time.
    4. Deterministic proposal via :func:`propose_archive_subfolder`.
    """
    subfolder, _ = get_archive_subfolder_with_source(
        conn, message_id, record, api_key=api_key, level=level, rules=rules
    )
    return subfolder


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
    provider_model: str | None = None,
    level: int = 1,
    rules: str = "",
) -> None:
    """Propose an archive subfolder for *record* via the unified triage agent
    and persist the hint.  Best-effort — failures are silently swallowed so
    the board falls back to the deterministic proposal.

    Delegates to
    :func:`robotsix_auto_mail.triage.agent.classify_archive_destination`: the
    same agent (prompt, output schema and ``triage`` tier) that assigns the
    board column also picks the archive destination — there is no separate
    archive-classifier agent.  *rules* is the advisory guidance text (from
    ``MailConfig.triage_guidance``).
    """
    # Lazy import: ``triage.agent`` imports this module at load time.
    from robotsix_auto_mail.triage.agent import classify_archive_destination

    classify_archive_destination(
        conn,
        record,
        api_key=api_key,
        provider_model=provider_model,
        level=level,
        rules=rules,
    )


# ---------------------------------------------------------------------------
# Sender-key helpers
# ---------------------------------------------------------------------------


def _sender_key(sender: str) -> str:
    """Return the generalization key for *sender*.

    Extracts the bare email address (lowercased); falls back to the raw
    lowercased sender string when no address can be parsed.
    """
    address = parseaddr(sender)[1]
    return (address or sender).strip().lower()
