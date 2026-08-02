"""Tests for the component-wide LLM settings.

The provider key and the Langfuse credentials live in the canonical
``openrouter`` / ``langfuse`` blocks on ``MailAccountsConfig``, never on a
per-mailbox ``MailConfig``.
"""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.config import (
    MAIN_LLM_ALIAS,
    ConfigurationError,
    LangfuseConfig,
    LangfuseProject,
    MailAccount,
    MailAccountsConfig,
    MailConfig,
    OpenRouterConfig,
    resolve_llm_api_key,
    resolve_llm_provider_model,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _accounts(**kwargs: object) -> MailAccountsConfig:
    return MailAccountsConfig(
        accounts=[
            MailAccount(
                account_id="default",
                config=MailConfig(
                    imap_host="i", smtp_host="s", username="u", password="p"
                ),
            )
        ],
        default_account_id="default",
        **kwargs,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# The canonical blocks
# ---------------------------------------------------------------------------


def test_credentials_are_not_per_account() -> None:
    """A mailbox is not an LLM function — MailConfig carries no credentials."""
    assert "llm_api_key" not in MailConfig.model_fields
    assert "llm_provider_model" not in MailConfig.model_fields
    assert not [f for f in MailConfig.model_fields if f.startswith("langfuse")]


def test_blocks_default_to_unconfigured() -> None:
    """A config that declares neither block is valid and simply traces nothing."""
    accts = _accounts()
    assert accts.openrouter.key() == ""
    assert accts.langfuse.project() is None
    assert accts.langfuse.host == ""
    assert accts.llm_provider_model == ""


def test_openrouter_key_is_addressed_by_alias() -> None:
    accts = _accounts(
        openrouter=OpenRouterConfig(
            keys={MAIN_LLM_ALIAS: "sk-or-main", "other-function": "sk-or-other"}
        )
    )
    assert accts.openrouter.key() == "sk-or-main"
    assert accts.openrouter.key("other-function") == "sk-or-other"
    assert accts.openrouter.key("absent") == ""


def test_openrouter_key_is_masked_in_dumps() -> None:
    accts = _accounts(openrouter=OpenRouterConfig(keys={MAIN_LLM_ALIAS: "sk-or-main"}))
    assert "sk-or-main" not in str(accts.model_dump(mode="json"))


def test_langfuse_project_is_addressed_by_alias() -> None:
    accts = _accounts(
        langfuse=LangfuseConfig(
            host="https://langfuse.example.net",
            projects={
                MAIN_LLM_ALIAS: LangfuseProject(
                    public_key="pk-lf", secret_key="sk-lf", project_id="cm1"
                )
            },
        )
    )
    project = accts.langfuse.project()
    assert project is not None
    assert project.public_key == "pk-lf"
    assert project.secret_key.get_secret_value() == "sk-lf"
    assert project.is_configured()


def test_half_filled_langfuse_project_is_not_configured() -> None:
    """One key alone traces nothing — treat it as unconfigured, not broken."""
    project = LangfuseProject(public_key="pk-lf")
    assert not project.is_configured()


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_resolve_llm_api_key_from_the_openrouter_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accts = _accounts(
        openrouter=OpenRouterConfig(keys={MAIN_LLM_ALIAS: "sk-from-file"})
    )
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=accts
    ):
        assert resolve_llm_api_key() == "sk-from-file"


def test_resolve_llm_api_key_default_when_nothing_set() -> None:
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=_accounts()
    ):
        assert resolve_llm_api_key(raise_on_missing=False) == ""


def test_resolve_llm_api_key_names_the_canonical_key_when_missing() -> None:
    """The error has to say where to put the key, or it is not actionable."""
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=_accounts()
    ):
        with pytest.raises(
            ConfigurationError, match=f"openrouter.keys.{MAIN_LLM_ALIAS}"
        ):
            resolve_llm_api_key()


def test_resolve_llm_api_key_when_load_fails() -> None:
    """resolve_llm_api_key returns empty string when config loading fails."""
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        side_effect=ConfigurationError("no config"),
    ):
        assert resolve_llm_api_key(raise_on_missing=False) == ""


def test_resolve_llm_provider_model_is_component_wide() -> None:
    accts = _accounts(llm_provider_model="openrouter-deepseek")
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=accts
    ):
        assert resolve_llm_provider_model() == "openrouter-deepseek"
        assert resolve_llm_provider_model("explicit") == "explicit"
