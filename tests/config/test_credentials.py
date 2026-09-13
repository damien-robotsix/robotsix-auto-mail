"""Dedicated unit tests for the canonical credential blocks.

Covers :mod:`robotsix_auto_mail.config.credentials` directly:
``LangfuseProject`` / ``LangfuseConfig`` / ``OpenRouterConfig`` — the
single source of truth for the fleet's per-project LLM credentials.

The component-wide wiring (resolution via ``MailAccountsConfig``) is
exercised in ``test_config_llm.py``; here we test the models themselves —
the ``is_configured()`` matrix, alias accessors, secret masking, and the
frozen constraint.
"""

from __future__ import annotations

import pydantic
import pytest
from pydantic import SecretStr

from robotsix_auto_mail.config.credentials import (
    MAIN_LLM_ALIAS,
    LangfuseConfig,
    LangfuseProject,
    OpenRouterConfig,
)

# ---------------------------------------------------------------------------
# LangfuseProject.is_configured — the full matrix
# ---------------------------------------------------------------------------


def test_is_configured_true_only_when_both_keys_set() -> None:
    """Both public and secret set → configured (tracing is possible)."""
    project = LangfuseProject(public_key="pk-lf", secret_key=SecretStr("sk-lf"))
    assert project.is_configured() is True


def test_is_configured_false_when_public_key_empty() -> None:
    """A secret alone is not enough — an empty public key traces nothing."""
    project = LangfuseProject(secret_key=SecretStr("sk-lf"))
    assert project.is_configured() is False


def test_is_configured_false_when_secret_key_empty() -> None:
    """A public key alone is not enough — an empty secret traces nothing."""
    project = LangfuseProject(public_key="pk-lf")
    assert project.is_configured() is False


def test_is_configured_false_when_both_empty() -> None:
    """The default (unconfigured) project traces nothing."""
    assert LangfuseProject().is_configured() is False


# ---------------------------------------------------------------------------
# LangfuseProject — fields & defaults
# ---------------------------------------------------------------------------


def test_langfuse_project_defaults() -> None:
    """Every field defaults to empty; secret_key is an empty SecretStr."""
    project = LangfuseProject()
    assert project.public_key == ""
    assert project.secret_key.get_secret_value() == ""
    assert project.project_id == ""


def test_langfuse_project_project_id_roundtrips() -> None:
    """The optional project_id is stored verbatim when supplied."""
    project = LangfuseProject(project_id="proj-123")
    assert project.project_id == "proj-123"


# ---------------------------------------------------------------------------
# LangfuseConfig.project — alias lookup
# ---------------------------------------------------------------------------


def test_langfuse_config_defaults_to_empty() -> None:
    """A bare LangfuseConfig has no host and no projects."""
    cfg = LangfuseConfig()
    assert cfg.host == ""
    assert cfg.projects == {}
    assert cfg.project() is None


def test_langfuse_config_project_default_alias() -> None:
    """project() with no argument looks up MAIN_LLM_ALIAS."""
    proj = LangfuseProject(public_key="pk", secret_key=SecretStr("sk"))
    cfg = LangfuseConfig(projects={MAIN_LLM_ALIAS: proj})
    assert cfg.project() is proj


def test_langfuse_config_project_explicit_alias() -> None:
    """project(alias) returns the project stored under that alias."""
    other = LangfuseProject(public_key="pk-other")
    cfg = LangfuseConfig(projects={"other-function": other})
    assert cfg.project("other-function") is other


def test_langfuse_config_project_missing_alias_is_none() -> None:
    """An absent alias yields None (safe default, not KeyError)."""
    cfg = LangfuseConfig(projects={MAIN_LLM_ALIAS: LangfuseProject()})
    assert cfg.project("absent") is None


# ---------------------------------------------------------------------------
# OpenRouterConfig.key — alias lookup
# ---------------------------------------------------------------------------


def test_openrouter_config_defaults_to_empty() -> None:
    """A bare OpenRouterConfig has no keys; key() returns ''."""
    cfg = OpenRouterConfig()
    assert cfg.keys == {}
    assert cfg.key() == ""


def test_openrouter_key_default_alias() -> None:
    """key() with no argument returns the MAIN_LLM_ALIAS secret value."""
    cfg = OpenRouterConfig(keys={MAIN_LLM_ALIAS: SecretStr("sk-main")})
    assert cfg.key() == "sk-main"


def test_openrouter_key_explicit_alias() -> None:
    """key(alias) returns the secret value stored under that alias."""
    cfg = OpenRouterConfig(
        keys={
            MAIN_LLM_ALIAS: SecretStr("sk-main"),
            "other-function": SecretStr("sk-other"),
        }
    )
    assert cfg.key("other-function") == "sk-other"


def test_openrouter_key_missing_alias_is_empty_string() -> None:
    """An absent alias yields '' (safe default, not KeyError)."""
    cfg = OpenRouterConfig(keys={MAIN_LLM_ALIAS: SecretStr("sk-main")})
    assert cfg.key("absent") == ""


# ---------------------------------------------------------------------------
# Secret handling — never exposed in repr / str
# ---------------------------------------------------------------------------


def test_langfuse_secret_not_exposed_in_repr() -> None:
    """The Langfuse secret key never appears in repr() or str()."""
    project = LangfuseProject(
        public_key="pk-lf", secret_key=SecretStr("sk-supersecret")
    )
    assert "sk-supersecret" not in repr(project)
    assert "sk-supersecret" not in str(project)
    # The public (non-secret) key is fine to show.
    assert "pk-lf" in repr(project)


def test_openrouter_secret_not_exposed_in_repr() -> None:
    """The OpenRouter provider key never appears in repr() or str()."""
    cfg = OpenRouterConfig(keys={MAIN_LLM_ALIAS: SecretStr("sk-supersecret")})
    assert "sk-supersecret" not in repr(cfg)
    assert "sk-supersecret" not in str(cfg)


def test_secrets_not_exposed_in_json_dump() -> None:
    """model_dump(mode="json") must not leak either secret."""
    project = LangfuseProject(public_key="pk", secret_key=SecretStr("sk-lf-secret"))
    orc = OpenRouterConfig(keys={MAIN_LLM_ALIAS: SecretStr("sk-or-secret")})
    assert "sk-lf-secret" not in str(project.model_dump(mode="json"))
    assert "sk-or-secret" not in str(orc.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Frozen constraints — no mutation after construction
# ---------------------------------------------------------------------------


def test_langfuse_project_is_frozen() -> None:
    """LangfuseProject is frozen — assignment raises ValidationError."""
    project = LangfuseProject(public_key="pk")
    with pytest.raises(pydantic.ValidationError):
        project.public_key = "changed"  # type: ignore[misc]


def test_langfuse_config_is_frozen() -> None:
    """LangfuseConfig is frozen — assignment raises ValidationError."""
    cfg = LangfuseConfig(host="https://langfuse.example.net")
    with pytest.raises(pydantic.ValidationError):
        cfg.host = "https://evil.example.net"  # type: ignore[misc]


def test_openrouter_config_is_frozen() -> None:
    """OpenRouterConfig is frozen — assignment raises ValidationError."""
    cfg = OpenRouterConfig(keys={MAIN_LLM_ALIAS: SecretStr("sk")})
    with pytest.raises(pydantic.ValidationError):
        cfg.keys = {}  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Alias constant
# ---------------------------------------------------------------------------


def test_main_llm_alias_is_repo_name() -> None:
    """The component standard fixes the main alias as the bare repo name."""
    assert MAIN_LLM_ALIAS == "robotsix-auto-mail"
