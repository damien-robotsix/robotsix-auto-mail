"""Self-managed archive folder structure for robotsix-auto-mail.

robotsix-auto-mail manages its own archive folder hierarchy, independent of
any pre-existing mailbox layout.  On the first run a quick LLM call proposes
an appropriate layout (based on the mailbox's existing folders) rooted at
``robotsix-mail-archive``; the chosen structure is then remembered in the
``watermark`` table so subsequent runs reuse it without re-asking the LLM or
recreating folders.

The ``pydantic_ai`` and LLM-provider imports are lazy to keep module-load
time low and to avoid requiring the optional provider extra for the
deterministic import path, mirroring :mod:`robotsix_auto_mail.config.detect`.
"""

from __future__ import annotations

import json
import sqlite3
import typing

import pydantic

from robotsix_auto_mail.config import (
    resolve_llm_api_key,
)
from robotsix_auto_mail.core._constants import (
    _ARCHIVE_ROOT,
    _ARCHIVE_TAXONOMY_GUIDANCE,
)
from robotsix_auto_mail.core._llm_agent import _run_llm_agent
from robotsix_auto_mail.db.queries import get_watermark, set_watermark
from robotsix_auto_mail.errors import RobotsixMailError
from robotsix_auto_mail.imap import ImapClient, is_special_use

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Root folder under which all managed archive folders live.
ARCHIVE_ROOT: str = _ARCHIVE_ROOT

#: Watermark key owned by this module (the same way ``fetch.py`` owns
#: ``"imap_uid"``).
_ARCHIVE_WATERMARK_KEY = "archive_structure"

#: Shared LLM parameter documentation referenced by
#: :func:`determine_archive_structure` and :func:`setup_archive`.
#:
#: The ``api_key`` resolver differs between the two call sites — each
#: docstring notes the specific resolver used.
_LLM_PARAM_DOCS = """\
        api_key: OpenRouter API key.  Resolves with the precedence
            ``api_key`` argument → config file.
        provider_model: LLM provider-model identifier
            (e.g. ``"<provider>-<model>"``).  ``None`` (the default) falls
            back to the tier-level default model.
        level: LLM integer tier to use.  ``1`` (cheap, default)."""

# Referenced by docstrings via :data:`_LLM_PARAM_DOCS` cross-references;
# the assignment below keeps CodeQL's py/unused-global-variable quiet.
_ = _LLM_PARAM_DOCS


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ArchiveError(RobotsixMailError):
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
    level: int = 1,
) -> list[str]:
    """Ask an LLM to propose an archive folder layout under the root.

    Args:
        existing_folders: Names of the folders already present in the
            mailbox, used to inform the proposed layout.
        archive_root: Logical root folder name (e.g.
            ``"robotsix-mail-archive"``).  Used in the system prompt
            to anchor the proposed layout.
        api_key: See :data:`_LLM_PARAM_DOCS`.  Resolved by
            :func:`~robotsix_auto_mail.core._llm_agent._run_llm_agent`.
        provider_model: See :data:`_LLM_PARAM_DOCS`.
        level: See :data:`_LLM_PARAM_DOCS`.

    Returns:
        A list of sub-paths relative to the archive root (``/``-separated).

    Raises:
        ArchiveError: If the API key is missing, the LLM returns an invalid
            response, or any other error occurs.
    """
    # -- build the user message --
    user_message = "Existing mailbox folders:\n" + "\n".join(existing_folders)

    structure = _run_llm_agent(
        api_key=api_key,
        provider_model=provider_model,
        level=level,
        system_prompt=_build_archive_system_prompt(archive_root),
        output_model=ArchiveStructure,
        user_message=user_message,
        label="archive structure",
        what="archive structure",
        exc_type=ArchiveError,
    )
    return structure.folders


# ---------------------------------------------------------------------------
# Setup / persistence
# ---------------------------------------------------------------------------


def setup_archive(
    conn: sqlite3.Connection,
    client: ImapClient,
    *,
    archive_root: str = ARCHIVE_ROOT,
    api_key: str | None = None,
    provider_model: str | None = None,
    level: int = 1,
) -> list[str]:
    """Determine and persist the managed archive folder layout.

    On the first run (no persisted structure) this lists the mailbox's
    folders, optionally asks the LLM for an appropriate layout under the
    effective root, and persists the resulting full-name list in the
    ``watermark`` table.  On subsequent runs the persisted list is
    returned directly without listing folders or calling the LLM.

    **No IMAP folders are created here.**  The watermark records the
    proposed layout so the triage agent can reference it, but actual
    folder creation happens lazily when a message is archived into a
    destination (via ``_ensure_folder_hierarchy`` in the board-server
    adapters).

    When no LLM API key is resolvable the LLM is never called — the
    archive falls back to just the effective root folder so ingestion
    is never blocked.

    Args:
        conn: Open SQLite connection.
        client: Connected IMAP client.
        archive_root: Logical root folder name (e.g.
            ``"robotsix-mail-archive"``).
        api_key: See :data:`_LLM_PARAM_DOCS`.  Resolved by
            :func:`~robotsix_auto_mail.config.resolve_llm_api_key`.
        provider_model: See :data:`_LLM_PARAM_DOCS`.
        level: See :data:`_LLM_PARAM_DOCS`.

    Returns:
        The list of full archive folder names that were persisted.
    """
    effective_root = archive_root

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
            level=level,
        )
    else:
        subpaths = []

    # -- build the full set of folder names for the watermark --
    structure: list[str] = [effective_root]
    for subpath in subpaths:
        translated = subpath.replace("/", delimiter)
        structure.append(effective_root + delimiter + translated)

    # -- persist and return (NO eager IMAP folder creation) --
    set_watermark(
        conn,
        _ARCHIVE_WATERMARK_KEY,
        json.dumps({"delimiter": delimiter, "folders": structure}),
    )
    return structure


# ---------------------------------------------------------------------------
# Cleanup — remove empty auto-created archive subfolders
# ---------------------------------------------------------------------------


def cleanup_empty_archive_folders(
    client: ImapClient,
    *,
    archive_root: str = ARCHIVE_ROOT,
) -> tuple[int, int]:
    """Remove empty subfolders under *archive_root*, bottom-up.

    Lists every folder under the archive root, builds a parent→children
    tree, then walks from the deepest leaves upward.  A folder is deleted
    only when it is **empty** (``SELECT`` reports 0 messages) AND all of
    its child folders have already been deleted (or were empty and are
    being deleted in the same pass).  The archive root itself is never
    deleted.

    Args:
        client: Connected IMAP client.
        archive_root: Logical root folder name (e.g.
            ``"robotsix-mail-archive"``).

    Returns:
        ``(deleted, skipped)`` — the number of folders deleted and the
        number of folders examined but kept (non-empty or root).
    """
    # 1. List all folders; filter to those under (or equal to) the root.
    all_folders = client.list_folders()
    delimiter = next((f.delimiter for f in all_folders if f.delimiter), "/")
    root_prefix = f"{archive_root}{delimiter}"

    archive_names: set[str] = set()
    for f in all_folders:
        if f.name == archive_root or f.name.startswith(root_prefix):
            archive_names.add(f.name)

    if not archive_names or archive_names == {archive_root}:
        return (0, 0)

    # 2. Build a parent→children tree keyed by full folder name.
    children: dict[str, set[str]] = {}
    for name in archive_names:
        children.setdefault(name, set())
        parent_delim = name.rfind(delimiter)
        if parent_delim >= 0:
            parent = name[:parent_delim]
            if parent in archive_names:
                children.setdefault(parent, set()).add(name)

    # 3. Compute depth for each folder so we can walk bottom-up.
    depth: dict[str, int] = {
        name: name.count(delimiter) + (1 if name == archive_root else 0)
        for name in archive_names
    }
    # The root itself gets the shallowest depth.
    depth[archive_root] = 0
    by_depth = sorted(archive_names - {archive_root}, key=lambda n: -depth[n])

    # 4. Walk deepest-first; delete empty folders.
    deleted = 0
    skipped = 0
    for name in by_depth:
        # Skip if any child still exists (hasn't been deleted).
        if children.get(name):
            skipped += 1
            continue
        # Check message count.
        try:
            count = client.select_folder(name)
        except Exception:
            skipped += 1
            continue
        if count > 0:
            skipped += 1
            continue
        # Folder is empty with no children — delete it.
        try:
            client.delete_folder(name)
        except Exception:
            skipped += 1
            continue
        deleted += 1
        # Remove this folder from its parent's child set so the parent
        # can be considered for deletion if it becomes childless.
        parent_delim = name.rfind(delimiter)
        if parent_delim >= 0:
            parent = name[:parent_delim]
            if parent in children:
                children[parent].discard(name)

    return (deleted, skipped)
