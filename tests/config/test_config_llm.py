"""Tests for LLM configuration settings (llm_api_key, load_llm)."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.config import (
    ConfigurationError,
    MailAccount,
    MailAccountsConfig,
    MailConfig,
    load_llm,
)

# ---------------------------------------------------------------------------
# LLM settings (llm_api_key, llm_provider_model fields on MailConfig)
# ---------------------------------------------------------------------------


def test_llm_defaults_when_absent() -> None:
    """llm api key defaults to an empty string."""
    cfg = MailConfig(imap_host="i", smtp_host="s", username="u", password="p")
    assert cfg.llm_api_key == ""


def test_llm_api_key_redacted_in_repr() -> None:
    """repr()/str() must NOT leak the LLM API key."""
    cfg = MailConfig(
        imap_host="i",
        smtp_host="s",
        username="u",
        password="p",
        llm_api_key="sk-or-secret",
    )
    assert "sk-or-secret" not in repr(cfg)
    assert "sk-or-secret" not in str(cfg)
    assert "<redacted>" in repr(cfg)


def test_llm_api_key_set_explicitly() -> None:
    """llm_api_key can be set directly on MailConfig."""
    cfg = MailConfig(
        imap_host="i",
        smtp_host="s",
        username="u",
        password="p",
        llm_api_key="sk-from-constructor",
    )
    assert cfg.llm_api_key == "sk-from-constructor"


def test_llm_api_key_default_when_unset() -> None:
    """Without an explicit llm_api_key, it defaults to empty."""
    cfg = MailConfig(imap_host="i", smtp_host="s", username="u", password="p")
    assert cfg.llm_api_key == ""


def test_llm_provider_model_set_explicitly() -> None:
    """llm_provider_model can be set directly on MailConfig."""
    cfg = MailConfig(
        imap_host="i",
        smtp_host="s",
        username="u",
        password="p",
        llm_provider_model="openrouter-deepseek",
    )
    assert cfg.llm_provider_model == "openrouter-deepseek"


def test_load_llm_reads_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_llm reads llm_api_key from the loaded config."""
    accts = MailAccountsConfig(
        accounts=[
            MailAccount(
                account_id="default",
                config=MailConfig(
                    imap_host="i",
                    smtp_host="s",
                    username="u",
                    password="p",
                    llm_api_key="sk-from-file",
                ),
            )
        ],
        default_account_id="default",
    )

    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=accts
    ):
        assert load_llm() == "sk-from-file"


def test_load_llm_default_when_nothing_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_llm returns empty key when no llm_api_key configured."""
    accts = MailAccountsConfig(
        accounts=[
            MailAccount(
                account_id="default",
                config=MailConfig(
                    imap_host="i",
                    smtp_host="s",
                    username="u",
                    password="p",
                ),
            )
        ],
        default_account_id="default",
    )

    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=accts
    ):
        assert load_llm() == ""


def test_load_llm_when_load_fails() -> None:
    """load_llm returns empty string when config loading fails."""
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        side_effect=ConfigurationError("no config"),
    ):
        assert load_llm() == ""
