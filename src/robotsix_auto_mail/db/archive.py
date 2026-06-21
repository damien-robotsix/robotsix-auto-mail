"""Self-managed archive folder structure for robotsix-auto-mail.

robotsix-auto-mail manages its own archive folder hierarchy, independent of
any pre-existing mailbox layout.  On the first run a quick LLM call proposes
an appropriate layout (based on the mailbox's existing folders) rooted at
``robotsix-mail-archive``; the chosen structure is then remembered in the
``watermark`` table so subsequent runs reuse it without re-asking the LLM or
recreating folders.

The ``pydantic_ai`` and LLM-provider imports are lazy to keep module-load
time low and to avoid requiring the optional provider extra for the
deterministic import path, mirroring :mod:`robotsix_auto_mail.detect`.
"""

from __future__ import annotations

import json
import sqlite3
import typing

import pydantic
from robotsix_llmio.core import Tier, run_agent

from robotsix_auto_mail._constants import _ARCHIVE_TAXONOMY_GUIDANCE
from robotsix_auto_mail.config import (
    ConfigurationError,
    resolve_llm_api_key,
    resolve_llm_provider_model,
)
from robotsix_auto_mail.db import get_watermark, set_watermark
from robotsix_auto_mail.imap import ImapClient, is_special_use

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Root folder under which all managed archive folders live.
ARCHIVE_ROOT = "robotsix-mail-archive"

#: Watermark key owned by this module (the same way ``fetch.py`` owns
#: ``"imap_uid"``).
_ARCHIVE_WATERMARK_KEY = "archive_structure"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ArchiveError(Exception):
    """Raised when determining the archive structure via the LLM fails."""


# ---------------------------------------------------------------------------
# Pydantic model — structured LLM output contract
# ---------------------------------------------------------------------------


class ArchiveStructure(pydantic.BaseModel):
    """Structured output the LLM must return — validated by pydantic.

    Each entry in ``folders`` is a sub-path relative to the archive root,
    using ``/`` as the separator (the list may be empty → just the root).
    """

    folders: list[str] = pydantic.Field(default_factory=list)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def _build_archive_system_prompt(archive_root: str) -> str:
    """Build the LLM system prompt, rooted at *archive_root*."""
    return (
        "You are an email archive organisation expert. Given the list of "
        "folders that already exist in a user's mailbox, propose an "
        f"appropriate archive folder layout rooted at `{archive_root}`.\n"
        "\n"
        "Return a JSON object with a `folders` field: a list of sub-paths "
        f"relative to the root `{archive_root}`, using `/` as the hierarchy "
        "separator. Do NOT include the root itself in the list, and do NOT "
        "prefix entries with the root. The list may be empty if just the "
        "root is appropriate.\n"
        "\n"
        "Return ONLY the JSON object matching the schema — no explanation, no "
        "markdown fences."
        "\n"
        "Folder taxonomy rules:\n" + _ARCHIVE_TAXONOMY_GUIDANCE + "\n"
        "Existing folders whose top-level segment contains a dot (e.g. "
        "`tii-ae/apinvoice`, `lwn.net/lwn`) reflect a legacy `<domain>/<sender>` "
        "convention. Do NOT propagate this pattern into new folder proposals — "
        "re-home that content into semantic topic buckets instead.\n"
    )


# ---------------------------------------------------------------------------
# Core LLM call
# ---------------------------------------------------------------------------


def determine_archive_structure(
    existing_folders: list[str],
    *,
    archive_root: str = ARCHIVE_ROOT,
    api_key: str | None = None,
    provider_model: str | None = None,
    tier: Tier = Tier.CHEAP,
) -> list[str]:
    """Ask an LLM to propose an archive folder layout under the root.

    Args:
        existing_folders: Names of the folders already present in the
            mailbox, used to inform the proposed layout.
        api_key: OpenRouter API key.  Defaults to the ``LLM_API_KEY`` env
            var.  Required unless the env var is set.
        provider_model: LLM provider-model identifier
            (e.g. ``openrouter-deepseek``).  Defaults
            to ``LLM_PROVIDER_MODEL`` env var, then ``llm.provider_model`` in the config
            file, then ``"openrouter-deepseek"``.
        tier: LLM tier to use.  ``Tier.CHEAP`` (default).

    Returns:
        A list of sub-paths relative to the archive root (``/``-separated).

    Raises:
        ArchiveError: If the API key is missing, the LLM returns an invalid
            response, or any other error occurs.
    """
    # -- resolve API key --
    try:
        resolved_key = resolve_llm_api_key(api_key)
    except ConfigurationError as exc:
        raise ArchiveError(str(exc)) from exc

    # -- resolve provider-model --
    resolved_provider_model = resolve_llm_provider_model(provider_model)

    # -- lazy import so the rest of the CLI works without the
    #    LLM provider extra --
    from pydantic_ai import PromptedOutput
    from robotsix_llmio.core import get_provider_for_identifier

    # -- build agent --
    llm_provider = get_provider_for_identifier(
        identifier=resolved_provider_model, api_key=resolved_key
    )
    agent_handle = llm_provider.build_agent(
        level=1 if tier == Tier.CHEAP else 2,
        system_prompt=_build_archive_system_prompt(archive_root),
        output_type=PromptedOutput(ArchiveStructure),
    )

    # -- build the user message --
    user_message = "Existing mailbox folders:\n" + "\n".join(existing_folders)

    # -- call LLM --
    try:
        result = run_agent(
            agent_handle,
            lambda: agent_handle.run_sync(user_message),
            label="archive structure",
            what="archive structure",
            trace_input=user_message,
        )
    except Exception as exc:
        raise ArchiveError(str(exc)) from exc

    structure: ArchiveStructure = result.output
    return structure.folders


# ---------------------------------------------------------------------------
# Setup / persistence
# ---------------------------------------------------------------------------


def setup_archive(
    conn: sqlite3.Connection,
    client: ImapClient,
    *,
    archive_root: str = ARCHIVE_ROOT,
    archive_namespace: str = "",
    api_key: str | None = None,
    provider_model: str | None = None,
    tier: Tier = Tier.CHEAP,
) -> list[str]:
    """Ensure the managed archive folder structure exists and is remembered.

    On the first run (no persisted structure) this lists the mailbox's
    folders, asks the LLM for an appropriate layout under the effective
    root, creates the missing folders, and persists the resulting
    full-name list in the ``watermark`` table.  On subsequent runs
    the persisted list is returned directly without listing folders, calling
    the LLM, or creating anything.

    When no LLM API key is resolvable the LLM is never called — the archive
    falls back to just the effective root folder so ingestion is never
    blocked.

    Args:
        conn: Open SQLite connection.
        client: Connected IMAP client.
        archive_root: Logical root folder name (e.g.
            ``"robotsix-mail-archive"``).
        archive_namespace: Optional IMAP namespace prefix to prepend to
            *archive_root* (e.g. ``"INBOX."``).  The effective root
            becomes ``namespace + archive_root``.
        api_key: OpenRouter API key.  Defaults to the ``LLM_API_KEY`` env var.
        tier: LLM tier to use.  ``Tier.CHEAP`` (default).

    Returns:
        The list of full (namespaced) archive folder names that exist
        after setup.
    """
    # Effective root includes the namespace prefix when configured.
    effective_root = archive_namespace + archive_root

    # -- already-remembered short-circuit --
    remembered = get_watermark(conn, _ARCHIVE_WATERMARK_KEY)
    if remembered is not None:
        data = json.loads(remembered)
        if isinstance(data, list):
            return data  # old format
        return typing.cast(list[str], data["folders"])  # new format

    # -- first run: inspect the mailbox --
    existing = client.list_folders()
    delimiter = next((f.delimiter for f in existing if f.delimiter), "/")

    # -- determine relative sub-paths (LLM, or fall back to root only) --
    # System / special-use mailboxes (Gmail's ``[Gmail]/All Mail``, ``Sent
    # Mail``, ``Trash`` … and the ``[Gmail]`` parent, plus any RFC 6154
    # special-use folder) are not archive-topic folders, so they are excluded
    # from the layout the LLM proposes.  For non-Gmail mailboxes, whose
    # folders carry no special-use attributes, this filter is a no-op.
    informational_folders = [f.name for f in existing if not is_special_use(f)]
    resolved_key = resolve_llm_api_key(api_key, raise_on_missing=False)
    if resolved_key:
        subpaths = determine_archive_structure(
            informational_folders,
            archive_root=archive_root,
            api_key=resolved_key,
            provider_model=provider_model,
            tier=tier,
        )
    else:
        subpaths = []

    # -- build the full set of folder names to ensure --
    structure: list[str] = [effective_root]
    for subpath in subpaths:
        translated = subpath.replace("/", delimiter)
        structure.append(effective_root + delimiter + translated)

    # -- create only the missing targets --
    existing_names = {f.name for f in existing}
    for name in structure:
        if name not in existing_names:
            client.create_folder(name)

    # -- persist and return --
    set_watermark(
        conn,
        _ARCHIVE_WATERMARK_KEY,
        json.dumps({"delimiter": delimiter, "folders": structure}),
    )
    return structure
