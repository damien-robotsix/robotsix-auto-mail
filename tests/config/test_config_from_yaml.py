"""Tests for per-account config validation via MailAccountsConfig.model_validate().

The former mono-file YAML loader and ``from_yaml()`` have been removed.
Configuration is now loaded from JSON via ``robotsix_config`` and validated
by pydantic.  These tests exercise the model directly through construction
and ``model_validate()``.
"""

from __future__ import annotations

import pydantic
import pytest

from robotsix_auto_mail.config import (
    ConfigurationError,
    MailAccount,
    MailAccountsConfig,
    MailConfig,
)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _acct(account_id: str = "default", **overrides: object) -> MailAccount:
    base: dict[str, object] = {
        "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com",
        "username": "u",
        "password": "p",
    }
    base.update(overrides)
    return MailAccount(
        account_id=account_id,
        config=MailConfig(**base),  # type: ignore[arg-type]
    )


def _accounts(
    *accts: MailAccount,
    default_account_id: str = "default",
) -> MailAccountsConfig:
    return MailAccountsConfig(
        accounts=list(accts),
        default_account_id=default_account_id,
    )


# ---------------------------------------------------------------------------
# Direct construction of MailConfig — defaults
# ---------------------------------------------------------------------------


def test_mailconfig_direct_construction_defaults() -> None:
    """MailConfig can be constructed directly; db_path default is empty."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="u",
        password="p",
    )
    assert cfg.db_path == ""
    assert cfg.oauth2_provider == ""
    assert cfg.oauth2_tenant == "organizations"


# ---------------------------------------------------------------------------
# Full field coverage
# ---------------------------------------------------------------------------


def test_mailconfig_full_fields() -> None:
    """All fields can be set explicitly."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        imap_port=143,
        imap_tls_mode="starttls",
        smtp_port=465,
        smtp_tls_mode="direct-tls",
        db_path=".data/custom/db.db",
        imap_folder="Archive",
        llm_api_key="sk-top-level",
        llm_provider_model="openrouter-deepseek",
        ingest_interval_minutes=10,
        archive_root="custom-archive",
        archive_enabled=False,
        triage_on_ingest=False,
        triage_rules_path="/path/to/rules.md",
        oauth2_provider="microsoft",
        oauth2_tenant="contoso.onmicrosoft.com",
        langfuse_public_key="pk-lf-yaml",
        langfuse_secret_key="sk-lf-yaml",
        langfuse_base_url="https://langfuse.example.net",
        log_level="DEBUG",
        log_format="json",
    )
    assert cfg.imap_host == "imap.example.com"
    assert cfg.imap_port == 143
    assert cfg.imap_tls_mode == "starttls"
    assert cfg.imap_folder == "Archive"
    assert cfg.smtp_host == "smtp.example.com"
    assert cfg.smtp_port == 465
    assert cfg.smtp_tls_mode == "direct-tls"
    assert cfg.username == "user@example.com"
    assert cfg.password == "s3cret"
    assert cfg.oauth2_provider == "microsoft"
    assert cfg.oauth2_tenant == "contoso.onmicrosoft.com"
    assert cfg.db_path == ".data/custom/db.db"
    assert cfg.triage_on_ingest is False
    assert cfg.llm_api_key == "sk-top-level"
    assert cfg.llm_provider_model == "openrouter-deepseek"
    assert cfg.langfuse_public_key == "pk-lf-yaml"
    assert cfg.langfuse_secret_key == "sk-lf-yaml"
    assert cfg.langfuse_base_url == "https://langfuse.example.net"


def test_multi_account_with_label() -> None:
    """MailAccount supports an optional label."""
    acct = _acct("personal", imap_host="imap.example.com")
    assert acct.account_id == "personal"
    acct_with_label = MailAccount(
        account_id="personal",
        config=MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
            password="s3cret",
        ),
        label="Personal",
    )
    assert acct_with_label.label == "Personal"


# ---------------------------------------------------------------------------
# Defaults for missing optional fields
# ---------------------------------------------------------------------------


def test_mailconfig_defaults_for_missing_fields() -> None:
    """Fields not provided fall back to their defaults."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="u",
        password="p",
    )
    assert cfg.imap_port == 993
    assert cfg.imap_tls_mode == "direct-tls"
    assert cfg.smtp_port == 587
    assert cfg.smtp_tls_mode == "starttls"
    assert cfg.imap_folder == "INBOX"
    assert cfg.triage_on_ingest is True


def test_mailconfig_langfuse_defaults_when_absent() -> None:
    """Langfuse fields default to empty strings."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="u",
        password="p",
    )
    assert cfg.langfuse_public_key == ""
    assert cfg.langfuse_secret_key == ""
    assert cfg.langfuse_base_url == ""


def test_mailconfig_llm_defaults_when_absent() -> None:
    """LLM fields default to empty strings."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="u",
        password="p",
    )
    assert cfg.llm_api_key == ""
    assert cfg.llm_provider_model == ""


def test_mailconfig_oauth2_provider_and_tenant_defaults() -> None:
    """oauth2_provider/tenant default: empty provider, 'organizations' tenant."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="u",
        password="p",
    )
    assert cfg.oauth2_provider == ""
    assert cfg.oauth2_tenant == "organizations"


# ---------------------------------------------------------------------------
# Required-field validation
# ---------------------------------------------------------------------------


def test_mailconfig_missing_required_fields() -> None:
    """Missing required fields → pydantic ValidationError."""
    with pytest.raises(pydantic.ValidationError) as exc:
        MailConfig(
            imap_host="imap.example.com",
            # smtp_host missing
            # username missing
            # password missing
        )
    errors = str(exc.value)
    assert "smtp_host" in errors
    assert "username" in errors
    # password is required
    assert "password" in errors


def test_mailconfig_missing_password_ok_if_empty() -> None:
    """Explicit empty password is valid."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="",
    )
    assert cfg.password == ""
    assert cfg.username == "user@example.com"


def test_mailconfig_invalid_tls_mode() -> None:
    """Invalid TLS mode → pydantic ValidationError."""
    with pytest.raises(pydantic.ValidationError) as exc:
        MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="u",
            password="p",
            imap_tls_mode="bad-mode",
        )
    msg = str(exc.value)
    assert "imap_tls_mode" in msg
    assert "bad-mode" in msg


def test_mailconfig_wrong_type_for_field() -> None:
    """A field with the wrong type (port as a string) → pydantic ValidationError."""
    with pytest.raises(pydantic.ValidationError) as exc:
        MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="u",
            password="p",
            imap_port="not-a-number",  # type: ignore[arg-type]
        )
    assert "imap_port" in str(exc.value)


# ---------------------------------------------------------------------------
# MailAccountsConfig validation
# ---------------------------------------------------------------------------


def test_accounts_validation_single() -> None:
    """Single account is valid."""
    cfg = _accounts(_acct("default"))
    assert cfg.default.account_id == "default"
    assert len(cfg.accounts) == 1


def test_accounts_validation_empty_raises() -> None:
    """Empty accounts list → ConfigurationError."""
    with pytest.raises(ConfigurationError):
        MailAccountsConfig(accounts=[], default_account_id="x")


def test_accounts_duplicate_id_raises() -> None:
    """Duplicate account ids → ConfigurationError."""
    with pytest.raises(ConfigurationError):
        MailAccountsConfig(
            accounts=[
                _acct("dup", db_path=".data/a.db"),
                _acct("dup", db_path=".data/b.db"),
            ],
            default_account_id="dup",
        )


def test_accounts_unknown_default_raises() -> None:
    """Unknown default_account_id → ConfigurationError."""
    with pytest.raises(ConfigurationError):
        MailAccountsConfig(
            accounts=[_acct("a", db_path=".data/a.db")],
            default_account_id="nope",
        )


def test_accounts_duplicate_db_path_raises() -> None:
    """Duplicate db_path → ConfigurationError."""
    with pytest.raises(ConfigurationError):
        MailAccountsConfig(
            accounts=[
                _acct("a", db_path=".data/same.db"),
                _acct("b", db_path=".data/same.db"),
            ],
            default_account_id="a",
        )


def test_accounts_get_and_default_and_ids() -> None:
    cfg = _accounts(
        _acct("personal", db_path=".data/p.db"),
        _acct("work", db_path=".data/w.db"),
        default_account_id="personal",
    )
    assert cfg.ids() == ("personal", "work")
    assert cfg.get("work").account_id == "work"
    assert cfg.default.account_id == "personal"


def test_accounts_get_unknown_lists_valid_ids() -> None:
    cfg = _accounts(
        _acct("personal", db_path=".data/p.db"),
        _acct("work", db_path=".data/w.db"),
        default_account_id="personal",
    )
    with pytest.raises(ConfigurationError) as exc:
        cfg.get("missing")
    msg = str(exc.value)
    assert "personal" in msg
    assert "work" in msg


# ---------------------------------------------------------------------------
# model_validate with dict data
# ---------------------------------------------------------------------------


def test_model_validate_multi_account() -> None:
    """model_validate parses a dict matching the pydantic shape."""
    data = {
        "accounts": [
            {
                "account_id": "personal",
                "label": "Personal Gmail",
                "config": {
                    "imap_host": "imap.gmail.com",
                    "smtp_host": "smtp.gmail.com",
                    "username": "me@gmail.com",
                    "password": "",
                    "db_path": ".data/personal/mail.db",
                },
            },
            {
                "account_id": "work",
                "config": {
                    "imap_host": "imap.work.example.com",
                    "smtp_host": "smtp.work.example.com",
                    "username": "me@work.example.com",
                    "password": "",
                    "db_path": ".data/work/mail.db",
                },
            },
        ],
        "default_account_id": "personal",
    }
    cfg = MailAccountsConfig.model_validate(data)
    assert cfg.ids() == ("personal", "work")
    assert cfg.default.account_id == "personal"
    assert cfg.get("personal").label == "Personal Gmail"
    assert cfg.get("personal").config.imap_host == "imap.gmail.com"
    assert cfg.get("work").config.imap_host == "imap.work.example.com"


def test_model_validate_default_top_level_fields() -> None:
    """model_validate with minimal data uses defaults for optional fields."""
    data = {
        "accounts": [
            {
                "account_id": "a",
                "config": {
                    "imap_host": "i",
                    "smtp_host": "s",
                    "username": "u",
                    "password": "p",
                },
            }
        ],
        "default_account_id": "a",
    }
    cfg = MailAccountsConfig.model_validate(data)
    c = cfg.default.config
    assert c.llm_api_key == ""
    assert c.llm_provider_model == ""
    assert c.ingest_interval_minutes == 15
    assert c.archive_root == "robotsix-mail-archive"
    assert c.archive_enabled is True
    assert c.triage_on_ingest is True


def test_model_validate_wrong_type() -> None:
    """model_validate with wrong type raises ValidationError."""
    data = {
        "accounts": [
            {
                "account_id": "a",
                "config": {
                    "imap_host": "i",
                    "smtp_host": "s",
                    "username": "u",
                    "password": "p",
                    "imap_port": "not-a-number",
                },
            }
        ],
        "default_account_id": "a",
    }
    with pytest.raises(pydantic.ValidationError):
        MailAccountsConfig.model_validate(data)


def test_model_validate_invalid_tls_mode() -> None:
    """model_validate with invalid TLS mode raises ValidationError."""
    data = {
        "accounts": [
            {
                "account_id": "a",
                "config": {
                    "imap_host": "i",
                    "smtp_host": "s",
                    "username": "u",
                    "password": "p",
                    "imap_tls_mode": "bad-mode",
                },
            }
        ],
        "default_account_id": "a",
    }
    with pytest.raises(pydantic.ValidationError):
        MailAccountsConfig.model_validate(data)
