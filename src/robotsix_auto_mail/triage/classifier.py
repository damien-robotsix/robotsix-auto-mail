"""Deterministic triage classification and archive-subfolder proposals.

Archive-subfolder proposals (deterministic + per-message override storage +
optional per-message LLM hints) and sender-key helpers.  The learned
preferences that used to live in JSON watermark ledgers now live in the
human-readable ``triage_rules.md`` file (see :mod:`robotsix_auto_mail.triage.rules`),
which the archive-subfolder LLM proposal reads via its ``rules`` argument.
The ``pydantic_ai`` / LLM-provider imports stay lazy inside
``propose_archive_subfolder_llm`` to keep module-load time low.
"""

from __future__ import annotations

import json
import re
import sqlite3
from email.utils import parseaddr
from typing import cast

from robotsix_auto_mail.core._constants import _ARCHIVE_TAXONOMY_GUIDANCE
from robotsix_auto_mail.core._llm_agent import _run_llm_agent
from robotsix_auto_mail.config import (
    resolve_llm_api_key,
)
from robotsix_auto_mail.db import (
    VALID_TRIAGE_ACTIONS,
    MailRecord,
    get_watermark,
    set_watermark,
)
from robotsix_auto_mail.triage._constants import (
    _ARCHIVE_LLM_HINTS_WATERMARK_KEY,
    _ARCHIVE_OVERRIDES_WATERMARK_KEY,
)
from robotsix_auto_mail.triage.persistence import (
    ArchiveSubfolderProposal,
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
    """Read a JSON-serialised dict watermark, returning {} when absent or corrupt."""
    raw = get_watermark(conn, key)
    if raw is None:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


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


def get_archive_subfolder(
    conn: sqlite3.Connection,
    message_id: str,
    record: MailRecord,
    api_key: str = "",
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
        propose_archive_subfolder_llm(conn, record, api_key, rules=rules)
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
    provider_model: str | None = None,
    rules: str = "",
) -> None:
    """Run a cheap LLM to propose an archive subfolder for *record*
    and persist the hint.  Best-effort — failures are silently
    swallowed so the board falls back to the deterministic proposal.

    *rules* is the human-readable ``triage_rules.md`` content; when
    non-empty it is injected so the proposal honours the user's rules.
    """
    # -- resolve API key --
    resolved_key = resolve_llm_api_key(api_key, raise_on_missing=False)
    if not resolved_key:
        return  # No API key → silently return

    # -- load existing archive folders from watermark --
    archive_raw = get_watermark(conn, "archive_structure")
    existing_folders: list[str] = []
    if archive_raw is not None:
        try:
            data = json.loads(archive_raw)
            existing_folders = data if isinstance(data, list) else data["folders"]
        except json.JSONDecodeError, TypeError, KeyError:
            existing_folders = []

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
    if rules.strip():
        system_prompt += (
            "\nThe user's triage rules (honour these when they apply):\n"
            f"{rules.strip()}\n"
        )
    system_prompt += "\nFolder taxonomy rules:\n" + _ARCHIVE_TAXONOMY_GUIDANCE + "\n"

    # -- build user message --
    body_snippet = record.body_plain[:1000] if record.body_plain else ""
    user_message = (
        f"Sender: {record.sender}\n"
        f"Subject: {record.subject}\n"
        f"Body (first 1000 chars):\n{body_snippet}"
    )

    # -- call shared LLM helper (best-effort) --
    try:
        proposed: ArchiveSubfolderProposal = _run_llm_agent(
            api_key=resolved_key,
            provider_model=provider_model,
            level=1,
            system_prompt=system_prompt,
            output_model=ArchiveSubfolderProposal,
            user_message=user_message,
            label="archive subfolder proposal",
            what="archive subfolder proposal",
            exc_type=RuntimeError,
        )
        subfolder = normalize_archive_subfolder(proposed.subfolder)
        if not subfolder:
            return  # Empty / root-only / action-only proposal → don't persist

        # -- persist the hint --
        hints = _load_llm_archive_hints(conn)
        hints[record.message_id] = subfolder
        _save_llm_archive_hints(conn, hints)
    except Exception:
        # Any failure (import error, LLM error, pydantic validation, etc.)
        # → silently return so the board falls back to the deterministic proposal.
        return


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
