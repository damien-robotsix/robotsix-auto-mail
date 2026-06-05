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

import importlib
import os
import sys
from pathlib import Path

import pydantic
from robotsix_llmio.core import Tier
from robotsix_llmio.openrouter_deepseek import OpenRouterDeepseekProvider

from robotsix_auto_mail.config import _FIELD_SPECS, _REQUIRED, load_llm

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

#: Accepted ``DriftProposal.confidence`` levels.
_VALID_CONFIDENCE_LEVELS = frozenset({"low", "medium", "high"})


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
        if v not in _VALID_CONFIDENCE_LEVELS:
            raise ValueError(
                "confidence must be one of "
                f"{sorted(_VALID_CONFIDENCE_LEVELS)!r}; got {v!r}"
            )
        return v


class ConfigSyncResult(pydantic.BaseModel):
    """Structured output the LLM must return — validated by pydantic.

    An empty ``proposals`` list is the valid "no drift detected" outcome.
    """

    proposals: list[DriftProposal] = pydantic.Field(default_factory=list)


# ---------------------------------------------------------------------------
# Surface gathering
# ---------------------------------------------------------------------------


def _default_repo_root() -> Path:
    """Return the repo root, resolved the same way the checker does."""
    return Path(__file__).resolve().parent.parent.parent


def _render_mailconfig_surface() -> str:
    """Render the ``MailConfig`` dataclass surface from ``_FIELD_SPECS``."""
    lines = [
        "MailConfig fields (field | yaml_path | env_key | kind | default):"
    ]
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
            raise ConfigSyncError(
                f"Cannot read config surface {path}: {exc}"
            ) from exc
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
        "name, or \"\" if cross-cutting), and a `confidence` of `low`, "
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
    tier: Tier = Tier.CHEAP,
) -> ConfigSyncResult:
    """Ask an LLM to detect config drift across the four config surfaces.

    Args:
        repo_root: Repository root.  Defaults to auto-detection (resolved
            the same way the deterministic checker resolves it).
        api_key: OpenRouter API key.  Resolves with the precedence
            ``api_key`` argument → ``LLM_API_KEY`` env var →
            ``config.llm_api_key`` (via the config loader).
        tier: LLM tier to use.  ``Tier.CHEAP`` (default).

    Returns:
        A ``ConfigSyncResult`` whose ``proposals`` list is empty when no
        drift is detected.

    Raises:
        ConfigSyncError: If the API key is missing, a config surface cannot
            be read, the LLM returns an invalid response, or any other
            error occurs.
    """
    resolved_root = repo_root if repo_root is not None else _default_repo_root()

    # -- resolve API key (arg -> LLM_API_KEY env -> config.llm_api_key) --
    resolved_key = api_key or os.environ.get("LLM_API_KEY", "")
    if not resolved_key:
        resolved_key, _ = load_llm()
    if not resolved_key:
        raise ConfigSyncError(
            "No LLM API key found — set the LLM_API_KEY environment "
            "variable or add an `llm.api_key` entry to your config file"
        )

    # -- gather the four surfaces + the ground-truth mappings --
    surfaces = _read_config_surfaces(resolved_root)
    field_to_yaml, field_to_env = _load_field_mappings(resolved_root)

    # -- lazy import so the rest of the CLI works without pydantic_ai --
    from pydantic_ai import PromptedOutput

    # -- build agent --
    llm_provider = OpenRouterDeepseekProvider(api_key=resolved_key)
    agent_handle = llm_provider.build_agent(
        tier=tier,
        system_prompt=_build_config_sync_system_prompt(),
        output_type=PromptedOutput(ConfigSyncResult),
    )

    # -- build the user message --
    user_message = _build_user_message(surfaces, field_to_yaml, field_to_env)

    # -- call LLM --
    try:
        result = llm_provider.call_with_retry(
            lambda: agent_handle.run_sync(user_message),
            what="config drift detection",
        )
    except Exception as exc:
        raise ConfigSyncError(str(exc)) from exc
    finally:
        agent_handle.close()

    output: ConfigSyncResult = result.output
    return output
