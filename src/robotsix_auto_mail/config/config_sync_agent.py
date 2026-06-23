"""Optional LLM-driven config-drift advisory agent.

The deterministic ``scripts/config/check_config_sync.py`` checker is the
fast, free, exact CI gate — it catches drift that fits its hard-coded
``FIELD_TO_YAML`` / ``FIELD_TO_ENV`` mappings and rule set.  This module is
an *optional, operator-facing advisory tool* that **complements** (and does
NOT replace) that gate: it asks an LLM to compare the four config surfaces
(the ``MailConfig`` dataclass, the YAML template, ``.env.example`` and the
connecting docs) against those same ground-truth mappings and emit
human-readable drift proposals — catching *unanticipated* patterns the
deterministic rules cannot express.

The ``pydantic_ai`` import is lazy to keep module-load time low, mirroring
:mod:`robotsix_auto_mail.detect` and :mod:`robotsix_auto_mail.archive`.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pydantic
from robotsix_llmio.core import Tier

from robotsix_auto_mail._llm_agent import _run_llm_agent
from robotsix_auto_mail.config import (
    _FIELD_SPECS,
    _REQUIRED,
)
from robotsix_auto_mail.config.pydantic_utils import validate_confidence
from robotsix_auto_mail.db import get_watermark, set_watermark

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Config surfaces (repo-relative) the agent compares, besides the
#: ``MailConfig`` dataclass which is rendered from ``_FIELD_SPECS``.
_SURFACE_FILES: tuple[str, ...] = (
    "config/mail.local.example.yaml",
    ".env.example",
    "docs/connecting.md",
)
#: Watermark key owned by this module for the dedup memory ledger.
#:
#: The ledger is persisted in ``db.py``'s ``watermark`` key-value table —
#: NOT a separate on-disk file — using the same ``json.dumps`` /
#: ``json.loads`` round-trip :mod:`robotsix_auto_mail.archive` uses for its
#: ``archive_structure`` key.  Reusing the watermark table keeps a single
#: storage mechanism, a single DB file (``MailConfig.db_path``) and a single
#: backup / lifecycle story instead of introducing a parallel file format.
_LEDGER_WATERMARK_KEY = "config_sync_ledger"

#: Accepted :class:`LedgerEntry` states.  All three suppress re-proposal of a
#: finding once it has been recorded.
_VALID_LEDGER_STATES = frozenset({"pending", "accepted", "rejected"})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ConfigSyncError(Exception):
    """Raised when the config-drift advisory agent fails."""


# ---------------------------------------------------------------------------
# Pydantic models — structured LLM output contract
# ---------------------------------------------------------------------------


class DriftProposal(pydantic.BaseModel):
    """A single human-readable config-drift proposal."""

    title: str = pydantic.Field(..., min_length=1)
    body: str = pydantic.Field(..., min_length=1)
    #: ``MailConfig`` field name, or "" when the drift is cross-cutting.
    affected_field: str = pydantic.Field(default="")
    #: Confidence level — one of ``low`` / ``medium`` / ``high``.
    confidence: str = pydantic.Field(default="medium")

    @pydantic.field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, v: str) -> str:
        return validate_confidence(v)


class ConfigSyncResult(pydantic.BaseModel):
    """Structured output the LLM must return — validated by pydantic.

    An empty ``proposals`` list is the valid "no drift detected" outcome.
    """

    proposals: list[DriftProposal] = pydantic.Field(default_factory=list)


class LedgerEntry(pydantic.BaseModel):
    """One remembered finding in the dedup memory ledger.

    Keyed (in the ledger dict) by the finding's stable fingerprint.  The
    ``state`` tracks whether the operator has acted on the finding; any of
    ``pending`` / ``accepted`` / ``rejected`` suppresses re-proposal.
    """

    title: str
    affected_field: str = ""
    state: str = "pending"

    @pydantic.field_validator("state")
    @classmethod
    def _validate_state(cls, v: str) -> str:
        if v not in _VALID_LEDGER_STATES:
            raise ValueError(
                f"state must be one of {sorted(_VALID_LEDGER_STATES)!r}; got {v!r}"
            )
        return v


# ---------------------------------------------------------------------------
# Dedup memory ledger
# ---------------------------------------------------------------------------


def _proposal_fingerprint(proposal: DriftProposal) -> str:
    """Return a deterministic fingerprint identifying *proposal*.

    The fingerprint is derived from the **stable** identity fields only —
    ``affected_field`` and ``title`` (each stripped and lower-cased) — and
    hashed with SHA-256.  The ``body`` is deliberately EXCLUDED: the LLM
    rewords its prose between runs, so a body-sensitive fingerprint would
    treat the same finding as new every time and defeat dedup entirely.
    """
    raw = (
        f"{proposal.affected_field.strip().lower()}\x00{proposal.title.strip().lower()}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _load_ledger(conn: sqlite3.Connection) -> dict[str, LedgerEntry]:
    """Load the dedup ledger from the watermark table.

    Returns an empty dict when the ledger has never been written.
    """
    raw = get_watermark(conn, _LEDGER_WATERMARK_KEY)
    if raw is None:
        return {}
    data: dict[str, object] = json.loads(raw)
    return {
        fingerprint: LedgerEntry.model_validate(entry)
        for fingerprint, entry in data.items()
    }


def _save_ledger(conn: sqlite3.Connection, ledger: dict[str, LedgerEntry]) -> None:
    """Persist *ledger* to the watermark table (json round-trip)."""
    payload = {fingerprint: entry.model_dump() for fingerprint, entry in ledger.items()}
    set_watermark(conn, _LEDGER_WATERMARK_KEY, json.dumps(payload))


def record_and_filter_proposals(
    conn: sqlite3.Connection, proposals: list[DriftProposal]
) -> list[DriftProposal]:
    """Record genuinely-new proposals and filter out already-seen ones.

    A proposal is *new* iff its fingerprint is not already present in the
    ledger in ANY state — ``pending``, ``accepted`` and ``rejected`` all
    suppress re-proposal so an operator never sees the same finding twice.
    New proposals are recorded as ``pending`` and returned in input order;
    the ledger is only written when there is at least one new entry to avoid
    needless writes.
    """
    ledger = _load_ledger(conn)
    new_proposals: list[DriftProposal] = []
    for proposal in proposals:
        fingerprint = _proposal_fingerprint(proposal)
        if fingerprint in ledger:
            continue
        ledger[fingerprint] = LedgerEntry(
            title=proposal.title,
            affected_field=proposal.affected_field,
            state="pending",
        )
        new_proposals.append(proposal)
    if new_proposals:
        _save_ledger(conn, ledger)
    return new_proposals


def set_finding_state(conn: sqlite3.Connection, fingerprint: str, state: str) -> None:
    """Transition the ledger entry *fingerprint* to *state*.

    Intended for the CLI / Web API slices to mark a finding ``accepted`` or
    ``rejected``.  Raises :class:`ConfigSyncError` for an invalid *state* or
    an unknown *fingerprint*.
    """
    if state not in _VALID_LEDGER_STATES:
        raise ConfigSyncError(
            f"state must be one of {sorted(_VALID_LEDGER_STATES)!r}; got {state!r}"
        )
    ledger = _load_ledger(conn)
    entry = ledger.get(fingerprint)
    if entry is None:
        raise ConfigSyncError(f"No ledger finding with fingerprint {fingerprint!r}")
    ledger[fingerprint] = entry.model_copy(update={"state": state})
    _save_ledger(conn, ledger)


# ---------------------------------------------------------------------------
# Surface gathering
# ---------------------------------------------------------------------------


def _default_repo_root() -> Path:
    """Return the repo root, resolved the same way the checker does."""
    return Path(__file__).resolve().parent.parent.parent.parent


def _render_mailconfig_surface() -> str:
    """Render the ``MailConfig`` dataclass surface from ``_FIELD_SPECS``."""
    lines = ["MailConfig fields (field | yaml_path | env_key | kind | default):"]
    for spec in _FIELD_SPECS:
        if spec.default is _REQUIRED:
            default = "<required>"
        else:
            default = repr(spec.default)
        lines.append(
            f"- {spec.field_name} | {spec.yaml_path} | {spec.env_key} | "
            f"{spec.kind} | {default}"
        )
    return "\n".join(lines)


def _read_config_surfaces(repo_root: Path) -> dict[str, str]:
    """Read the four config surfaces, keyed by a human-readable label."""
    surfaces: dict[str, str] = {}
    for rel in _SURFACE_FILES:
        path = repo_root / rel
        try:
            surfaces[rel] = path.read_text()
        except OSError as exc:
            raise ConfigSyncError(f"Cannot read config surface {path}: {exc}") from exc
    surfaces["MailConfig dataclass"] = _render_mailconfig_surface()
    return surfaces


def _load_field_mappings(
    repo_root: Path,
) -> tuple[dict[str, str], dict[str, str]]:
    """Import the ground-truth mappings from the deterministic checker.

    The checker lives at ``scripts/config/check_config_sync.py`` (not on the
    package path), so its directory is added to ``sys.path`` before import —
    the same bootstrap ``tests/config/test_config_sync.py`` uses.
    """
    scripts_dir = repo_root / "scripts" / "config"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        module = importlib.import_module("check_config_sync")
    except ImportError as exc:
        raise ConfigSyncError(
            f"Cannot import check_config_sync from {scripts_dir}: {exc}"
        ) from exc
    field_to_yaml: dict[str, str] = module.FIELD_TO_YAML
    field_to_env: dict[str, str] = module.FIELD_TO_ENV
    return field_to_yaml, field_to_env


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def _build_config_sync_system_prompt() -> str:
    """Build the LLM system prompt for config-drift advisory detection."""
    return (
        "You are a configuration-consistency auditor for a Python project. "
        "You are given an authoritative mapping of each `MailConfig` field "
        "to its YAML key and environment variable, followed by the text of "
        "four configuration surfaces: the `MailConfig` dataclass, the YAML "
        "template, the `.env.example` file, and the connecting docs.\n"
        "\n"
        "Compare the four surfaces against the authoritative mapping and "
        "against each other. Emit a human-readable proposal for any "
        "divergence — for example a field described with the wrong "
        "semantics in prose, a YAML key that looks meaningful but is not in "
        "the mapping, an inconsistent default between surfaces, or "
        "contradictory documentation. Each proposal has a `title`, a `body` "
        "explaining the drift, an `affected_field` (the `MailConfig` field "
        'name, or "" if cross-cutting), and a `confidence` of `low`, '
        "`medium`, or `high`.\n"
        "\n"
        "Return a JSON object with a `proposals` field. Return an EMPTY "
        "`proposals` list when the surfaces are consistent.\n"
        "\n"
        "Return ONLY the JSON object matching the schema — no explanation, "
        "no markdown fences."
    )


def _render_mappings(
    field_to_yaml: dict[str, str], field_to_env: dict[str, str]
) -> str:
    """Render the ground-truth field/YAML/env mapping for the prompt."""
    lines = ["Authoritative field -> YAML key -> env var mapping:"]
    for field_name, yaml_key in field_to_yaml.items():
        env_key = field_to_env.get(field_name, "")
        lines.append(f"- {field_name}: yaml=`{yaml_key}` env=`{env_key}`")
    return "\n".join(lines)


def _build_user_message(
    surfaces: dict[str, str],
    field_to_yaml: dict[str, str],
    field_to_env: dict[str, str],
) -> str:
    """Assemble the user message from the mapping + the four surfaces."""
    sections = [_render_mappings(field_to_yaml, field_to_env)]
    for label, text in surfaces.items():
        sections.append(f"===== {label} =====\n{text}")
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Core agent
# ---------------------------------------------------------------------------


def run_config_sync_agent(
    *,
    repo_root: Path | None = None,
    api_key: str | None = None,
    provider_model: str | None = None,
    tier: Tier = Tier.CHEAP,
    conn: sqlite3.Connection | None = None,
) -> ConfigSyncResult:
    """Ask an LLM to detect config drift across the four config surfaces.

    Args:
        repo_root: Repository root.  Defaults to auto-detection (resolved
            the same way the deterministic checker resolves it).
        api_key: OpenRouter API key.  Resolves with the precedence
            ``api_key`` argument → ``LLM_API_KEY`` env var →
            ``config.llm_api_key`` (via the config loader).
        provider: LLM backend name (e.g. ``openrouter-deepseek``).
            Resolves with the precedence ``provider`` argument →
            ``LLM_PROVIDER_MODEL`` env var → ``config.llm_provider_model`` (via
            :func:`load_llm_provider_model`).
        tier: LLM tier to use.  ``Tier.CHEAP`` (default).
        conn: Optional open SQLite connection.  When provided, the result
            is passed through the dedup memory ledger
            (:func:`record_and_filter_proposals`): findings already seen in
            a prior run are filtered out and genuinely-new findings are
            recorded as ``pending``.  When ``None`` (default) the LLM
            proposals are returned unchanged.

    Returns:
        A ``ConfigSyncResult`` whose ``proposals`` list is empty when no
        drift is detected.

    Raises:
        ConfigSyncError: If the API key is missing, a config surface cannot
            be read, the LLM returns an invalid response, or any other
            error occurs.
    """
    resolved_root = repo_root if repo_root is not None else _default_repo_root()
    surfaces = _read_config_surfaces(resolved_root)
    field_to_yaml, field_to_env = _load_field_mappings(resolved_root)
    user_message = _build_user_message(surfaces, field_to_yaml, field_to_env)

    output = _run_llm_agent(
        api_key=api_key,
        provider_model=provider_model,
        tier=tier,
        system_prompt=_build_config_sync_system_prompt(),
        output_model=ConfigSyncResult,
        user_message=user_message,
        label="config drift detection",
        what="config drift detection",
        exc_type=ConfigSyncError,
    )

    if conn is not None:
        output = ConfigSyncResult(
            proposals=record_and_filter_proposals(conn, output.proposals)
        )
    return output
