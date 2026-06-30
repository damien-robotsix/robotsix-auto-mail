"""Tests for the multi-account configuration layer."""

from __future__ import annotations

import os
import textwrap
import warnings
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.config import (
    ConfigurationError,
    FailedAccountEntry,
    MailAccount,
    MailAccountsConfig,
    MailConfig,
    load_accounts,
    render_accounts_yaml,
)

# ---------------------------------------------------------------------------
# MailAccount validation
# ---------------------------------------------------------------------------


def _cfg(**overrides: object) -> MailConfig:
    base: dict[str, object] = {
        "imap_host": "i",
        "smtp_host": "s",
        "username": "u",
        "password": "p",
    }
    base.update(overrides)
    return MailConfig(**base)  # type: ignore[arg-type]


def test_mailaccount_requires_non_empty_id() -> None:
    with pytest.raises(ConfigurationError):
        MailAccount(account_id="", config=_cfg())


def test_mailaccount_rejects_bad_charset() -> None:
    with pytest.raises(ConfigurationError):
        MailAccount(account_id="bad id!", config=_cfg())


def test_mailaccount_accepts_valid_id() -> None:
    acct = MailAccount(account_id="personal-1.0_x", config=_cfg(), label="Me")
    assert acct.account_id == "personal-1.0_x"
    assert acct.label == "Me"


# ---------------------------------------------------------------------------
# MailAccountsConfig helpers / validation
# ---------------------------------------------------------------------------


def _accounts() -> MailAccountsConfig:
    return MailAccountsConfig(
        accounts=(
            MailAccount("personal", _cfg(db_path=".data/p.db")),
            MailAccount("work", _cfg(db_path=".data/w.db")),
        ),
        default_account_id="personal",
    )


def test_accounts_get_and_default_and_ids() -> None:
    cfg = _accounts()
    assert cfg.ids() == ("personal", "work")
    assert cfg.get("work").account_id == "work"
    assert cfg.default.account_id == "personal"


def test_accounts_get_unknown_lists_valid_ids() -> None:
    cfg = _accounts()
    with pytest.raises(ConfigurationError) as exc:
        cfg.get("missing")
    msg = str(exc.value)
    assert "personal" in msg
    assert "work" in msg


def test_accounts_empty_raises() -> None:
    with pytest.raises(ConfigurationError):
        MailAccountsConfig(accounts=(), default_account_id="x")


def test_accounts_duplicate_id_raises() -> None:
    with pytest.raises(ConfigurationError):
        MailAccountsConfig(
            accounts=(
                MailAccount("dup", _cfg(db_path=".data/a.db")),
                MailAccount("dup", _cfg(db_path=".data/b.db")),
            ),
            default_account_id="dup",
        )


def test_accounts_duplicate_db_path_raises() -> None:
    with pytest.raises(ConfigurationError):
        MailAccountsConfig(
            accounts=(
                MailAccount("a", _cfg(db_path=".data/same.db")),
                MailAccount("b", _cfg(db_path=".data/same.db")),
            ),
            default_account_id="a",
        )


def test_accounts_unknown_default_raises() -> None:
    with pytest.raises(ConfigurationError):
        MailAccountsConfig(
            accounts=(MailAccount("a", _cfg(db_path=".data/a.db")),),
            default_account_id="nope",
        )


# ---------------------------------------------------------------------------
# Backward compat (criterion 2)
# ---------------------------------------------------------------------------


def test_from_yaml_single_account_rejected(tmp_path: Path) -> None:
    """A mono-shaped YAML file is rejected with an actionable error."""
    path = tmp_path / "mail.local.yaml"
    path.write_text(
        "imap:\n  host: imap.example.com\n"
        "smtp:\n  host: smtp.example.com\n"
        "auth:\n  username: user@example.com\n  password: s3cret\n"
    )
    with pytest.raises(ConfigurationError) as excinfo:
        MailAccountsConfig.from_yaml(str(path))
    message = str(excinfo.value)
    assert "migrate-config" in message
    assert "detect" in message


def test_from_env_single_account_loads_silently() -> None:
    """A complete ``MAIL_*`` env loads as the 'default' account, no warning."""
    env = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "user@example.com",
        "MAIL_PASSWORD": "s3cret",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            accounts = MailAccountsConfig.from_env()
        assert accounts.ids() == ("default",)
        only = accounts.accounts[0]
        assert only.account_id == "default"
        assert only.config.db_path == ".data/mail.db"
        assert only.config == MailConfig.from_env()


# ---------------------------------------------------------------------------
# Multi-account YAML (criteria 3 & 4)
# ---------------------------------------------------------------------------


def test_from_yaml_multi_account_example() -> None:
    accounts = MailAccountsConfig.from_yaml("docs/config/mail.local.example.yaml")
    assert accounts.ids() == ("personal", "work")
    assert accounts.default_account_id == "personal"

    personal = accounts.get("personal")
    assert personal.label == "Personal Gmail"
    assert personal.config.imap_host == "imap.gmail.com"
    assert personal.config.username == "me@gmail.com"
    assert personal.config.db_path == ".data/personal/mail.db"

    work = accounts.get("work")
    assert work.config.imap_host == "imap.work.example.com"
    assert work.config.db_path == ".data/work/mail.db"

    assert accounts.default.account_id == "personal"


def test_render_accounts_yaml_microsoft_oauth2_block() -> None:
    """A Microsoft OAuth2 account emits oauth2_provider/tenant and NO password."""
    account = MailAccount(
        "office365",
        _cfg(
            imap_host="outlook.office365.com",
            smtp_host="smtp.office365.com",
            username="me@contoso.com",
            password="",
            oauth2_provider="microsoft",
            oauth2_tenant="organizations",
            db_path=".data/office365/mail.db",
        ),
    )
    text = render_accounts_yaml([account], "office365")
    assert 'oauth2_provider: "microsoft"' in text
    assert 'oauth2_tenant: "organizations"' in text
    assert "password:" not in text


def test_render_accounts_yaml_microsoft_round_trips(tmp_path: Path) -> None:
    """The rendered Microsoft OAuth2 account parses back via from_yaml()."""
    account = MailAccount(
        "office365",
        _cfg(
            imap_host="outlook.office365.com",
            smtp_host="smtp.office365.com",
            username="me@contoso.com",
            password="",
            oauth2_provider="microsoft",
            oauth2_tenant="organizations",
            db_path=".data/office365/mail.db",
        ),
    )
    yaml_file = tmp_path / "accts.yaml"
    yaml_file.write_text(render_accounts_yaml([account], "office365"))
    parsed = MailAccountsConfig.from_yaml(yaml_file)
    cfg = parsed.get("office365").config
    assert cfg.oauth2_provider == "microsoft"
    assert cfg.oauth2_tenant == "organizations"
    assert cfg.password == ""


def test_render_accounts_yaml_password_block_unchanged() -> None:
    """A non-OAuth2 account still emits a password line and no oauth2 fields."""
    account = MailAccount("p", _cfg(db_path=".data/p/mail.db"))
    text = render_accounts_yaml([account], "p")
    assert "password:" in text
    assert "oauth2_provider" not in text


def test_from_yaml_multi_account_db_path_default(tmp_path: Path) -> None:
    yaml_file = tmp_path / "accts.yaml"
    yaml_file.write_text(
        """\
accounts:
  - id: alpha
    imap:
      host: imap.alpha.com
    smtp:
      host: smtp.alpha.com
    auth:
      username: a
      password: p
  - id: beta
    imap:
      host: imap.beta.com
    smtp:
      host: smtp.beta.com
    auth:
      username: b
      password: p
"""
    )
    accounts = MailAccountsConfig.from_yaml(yaml_file)
    assert accounts.get("alpha").config.db_path == ".data/alpha/mail.db"
    assert accounts.get("beta").config.db_path == ".data/beta/mail.db"
    # default is the first entry when default_account is absent
    assert accounts.default_account_id == "alpha"


def test_from_yaml_multi_account_duplicate_db_path_raises(tmp_path: Path) -> None:
    yaml_file = tmp_path / "dup.yaml"
    yaml_file.write_text(
        """\
accounts:
  - id: alpha
    imap:
      host: imap.alpha.com
    smtp:
      host: smtp.alpha.com
    auth:
      username: a
      password: p
    store:
      path: .data/shared.db
  - id: beta
    imap:
      host: imap.beta.com
    smtp:
      host: smtp.beta.com
    auth:
      username: b
      password: p
    store:
      path: .data/shared.db
"""
    )
    with pytest.raises(ConfigurationError):
        MailAccountsConfig.from_yaml(yaml_file)


def test_from_yaml_multi_account_duplicate_id_raises(tmp_path: Path) -> None:
    yaml_file = tmp_path / "dupid.yaml"
    yaml_file.write_text(
        """\
accounts:
  - id: same
    imap:
      host: imap.alpha.com
    smtp:
      host: smtp.alpha.com
    auth:
      username: a
      password: p
  - id: same
    imap:
      host: imap.beta.com
    smtp:
      host: smtp.beta.com
    auth:
      username: b
      password: p
"""
    )
    with pytest.raises(ConfigurationError):
        MailAccountsConfig.from_yaml(yaml_file)


def test_from_yaml_explicit_default_account(tmp_path: Path) -> None:
    yaml_file = tmp_path / "default.yaml"
    yaml_file.write_text(
        """\
default_account: beta
accounts:
  - id: alpha
    imap:
      host: imap.alpha.com
    smtp:
      host: smtp.alpha.com
    auth:
      username: a
      password: p
  - id: beta
    imap:
      host: imap.beta.com
    smtp:
      host: smtp.beta.com
    auth:
      username: b
      password: p
"""
    )
    accounts = MailAccountsConfig.from_yaml(yaml_file)
    assert accounts.default.account_id == "beta"


# ---------------------------------------------------------------------------
# Env namespacing (criterion 5)
# ---------------------------------------------------------------------------


def _multi_env() -> dict[str, str]:
    return {
        "MAIL_ACCOUNTS_0_ID": "personal",
        "MAIL_ACCOUNTS_0_LABEL": "Personal",
        "MAIL_ACCOUNTS_0_IMAP_HOST": "imap.personal.com",
        "MAIL_ACCOUNTS_0_SMTP_HOST": "smtp.personal.com",
        "MAIL_ACCOUNTS_0_USERNAME": "me@personal.com",
        "MAIL_ACCOUNTS_0_PASSWORD": "p0",
        "MAIL_ACCOUNTS_1_ID": "work",
        "MAIL_ACCOUNTS_1_IMAP_HOST": "imap.work.com",
        "MAIL_ACCOUNTS_1_SMTP_HOST": "smtp.work.com",
        "MAIL_ACCOUNTS_1_USERNAME": "me@work.com",
        "MAIL_ACCOUNTS_1_PASSWORD": "p1",
    }


def test_from_env_multi_account() -> None:
    with mock.patch.dict(os.environ, _multi_env(), clear=True):
        accounts = MailAccountsConfig.from_env()
    assert accounts.ids() == ("personal", "work")
    personal = accounts.get("personal")
    assert personal.label == "Personal"
    assert personal.config.imap_host == "imap.personal.com"
    assert personal.config.username == "me@personal.com"
    assert personal.config.db_path == ".data/personal/mail.db"
    work = accounts.get("work")
    assert work.label is None
    assert work.config.imap_host == "imap.work.com"
    assert work.config.db_path == ".data/work/mail.db"
    assert accounts.default.account_id == "personal"


def test_from_env_multi_account_explicit_default_and_db_path() -> None:
    env = _multi_env()
    env["MAIL_ACCOUNTS_DEFAULT"] = "work"
    env["MAIL_ACCOUNTS_1_DB_PATH"] = ".data/custom-work.db"
    with mock.patch.dict(os.environ, env, clear=True):
        accounts = MailAccountsConfig.from_env()
    assert accounts.default.account_id == "work"
    assert accounts.get("work").config.db_path == ".data/custom-work.db"


def test_from_env_non_contiguous_gap_raises() -> None:
    env = {
        "MAIL_ACCOUNTS_0_ID": "a",
        "MAIL_ACCOUNTS_0_IMAP_HOST": "imap.a.com",
        "MAIL_ACCOUNTS_0_SMTP_HOST": "smtp.a.com",
        "MAIL_ACCOUNTS_0_USERNAME": "a",
        "MAIL_ACCOUNTS_0_PASSWORD": "p",
        "MAIL_ACCOUNTS_2_ID": "c",
        "MAIL_ACCOUNTS_2_IMAP_HOST": "imap.c.com",
        "MAIL_ACCOUNTS_2_SMTP_HOST": "smtp.c.com",
        "MAIL_ACCOUNTS_2_USERNAME": "c",
        "MAIL_ACCOUNTS_2_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError):
            MailAccountsConfig.from_env()


def test_from_env_invalid_account_id_raises() -> None:
    env = {
        "MAIL_ACCOUNTS_0_ID": "bad id!",
        "MAIL_ACCOUNTS_0_IMAP_HOST": "imap.a.com",
        "MAIL_ACCOUNTS_0_SMTP_HOST": "smtp.a.com",
        "MAIL_ACCOUNTS_0_USERNAME": "a",
        "MAIL_ACCOUNTS_0_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError):
            MailAccountsConfig.from_env()


def test_from_env_missing_account_id_raises() -> None:
    env = {
        "MAIL_ACCOUNTS_0_IMAP_HOST": "imap.a.com",
        "MAIL_ACCOUNTS_0_SMTP_HOST": "smtp.a.com",
        "MAIL_ACCOUNTS_0_USERNAME": "a",
        "MAIL_ACCOUNTS_0_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError):
            MailAccountsConfig.from_env()


# ---------------------------------------------------------------------------
# load_accounts cascade (criterion 6)
# ---------------------------------------------------------------------------


def test_load_accounts_env_first_multi() -> None:
    with mock.patch.dict(os.environ, _multi_env(), clear=True):
        accounts = load_accounts()
    assert accounts.ids() == ("personal", "work")


def test_load_accounts_env_first_single() -> None:
    env = {
        "MAIL_IMAP_HOST": "imap.env.com",
        "MAIL_SMTP_HOST": "smtp.env.com",
        "MAIL_USERNAME": "env_user",
        "MAIL_PASSWORD": "env_pass",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        accounts = load_accounts()
    assert accounts.ids() == ("default",)
    assert accounts.default.config.imap_host == "imap.env.com"


def test_load_accounts_single_yaml_rejected(tmp_path: Path) -> None:
    """A mono YAML file is rejected by ``load_accounts`` with an actionable error."""
    yaml_file = tmp_path / "mail.local.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.file.com
smtp:
  host: smtp.file.com
auth:
  username: file_user
  password: file_pass
"""
    )
    env = {"MAIL_CONFIG_PATH": str(yaml_file)}
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as excinfo:
            load_accounts()
    message = str(excinfo.value)
    assert "robotsix-auto-mail migrate-config" in message
    assert "robotsix-auto-mail detect" in message


def test_load_accounts_falls_back_to_multi_yaml() -> None:
    env = {"MAIL_CONFIG_PATH": "docs/config/mail.local.example.yaml"}
    with mock.patch.dict(os.environ, env, clear=True):
        accounts = load_accounts()
    assert accounts.ids() == ("personal", "work")


def test_load_accounts_reraises_invalid_env_value(tmp_path: Path) -> None:
    yaml_file = tmp_path / "mail.local.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.file.com
smtp:
  host: smtp.file.com
auth:
  username: file_user
  password: file_pass
"""
    )
    env = {
        "MAIL_CONFIG_PATH": str(yaml_file),
        "MAIL_IMAP_HOST": "imap.env.com",
        "MAIL_SMTP_HOST": "smtp.env.com",
        "MAIL_USERNAME": "env_user",
        "MAIL_PASSWORD": "env_pass",
        "MAIL_IMAP_PORT": "not-a-number",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            load_accounts()
    assert "MAIL_IMAP_PORT" in str(exc.value)


# ---------------------------------------------------------------------------
# Top-level llm: / langfuse: sections (application-wide)
# ---------------------------------------------------------------------------


def test_from_yaml_top_level_llm_applied_to_all_accounts(tmp_path: Path) -> None:
    """Top-level llm: section populates llm_api_key on every account."""
    yaml_file = tmp_path / "accts.yaml"
    yaml_file.write_text(
        """\
llm:
  api_key: sk-global
accounts:
  - id: a
    imap:
      host: imap.a.com
    smtp:
      host: smtp.a.com
    auth:
      username: a
      password: p
  - id: b
    imap:
      host: imap.b.com
    smtp:
      host: smtp.b.com
    auth:
      username: b
      password: p
"""
    )
    accounts = MailAccountsConfig.from_yaml(yaml_file)
    assert accounts.get("a").config.llm_api_key == "sk-global"
    assert accounts.get("a").config.llm_provider_model == ""
    assert accounts.get("b").config.llm_api_key == "sk-global"
    assert accounts.get("b").config.llm_provider_model == ""


def test_from_yaml_top_level_langfuse_applied_to_all_accounts(
    tmp_path: Path,
) -> None:
    """Top-level langfuse: section populates langfuse fields on every account."""
    yaml_file = tmp_path / "accts.yaml"
    yaml_file.write_text(
        """\
langfuse:
  public_key: pk-lf-global
  secret_key: sk-lf-global
  base_url: https://langfuse.example.com
accounts:
  - id: a
    imap:
      host: imap.a.com
    smtp:
      host: smtp.a.com
    auth:
      username: a
      password: p
  - id: b
    imap:
      host: imap.b.com
    smtp:
      host: smtp.b.com
    auth:
      username: b
      password: p
"""
    )
    accounts = MailAccountsConfig.from_yaml(yaml_file)
    cfg_a = accounts.get("a").config
    assert cfg_a.langfuse_public_key == "pk-lf-global"
    assert cfg_a.langfuse_secret_key == "sk-lf-global"
    assert cfg_a.langfuse_base_url == "https://langfuse.example.com"
    cfg_b = accounts.get("b").config
    assert cfg_b.langfuse_public_key == "pk-lf-global"
    assert cfg_b.langfuse_secret_key == "sk-lf-global"
    assert cfg_b.langfuse_base_url == "https://langfuse.example.com"


def test_from_yaml_top_level_llm_wins_over_per_account_default(
    tmp_path: Path,
) -> None:
    """Top-level llm: values override the empty-string default on every account."""
    yaml_file = tmp_path / "accts.yaml"
    yaml_file.write_text(
        """\
llm:
  api_key: sk-global
accounts:
  - id: a
    imap:
      host: imap.a.com
    smtp:
      host: smtp.a.com
    auth:
      username: a
      password: p
"""
    )
    accounts = MailAccountsConfig.from_yaml(yaml_file)
    assert accounts.get("a").config.llm_api_key == "sk-global"


def test_from_yaml_per_account_llm_rejected(tmp_path: Path) -> None:
    """Per-account llm: block raises ConfigurationError with actionable message."""
    yaml_file = tmp_path / "accts.yaml"
    yaml_file.write_text(
        """\
accounts:
  - id: personal
    imap:
      host: imap.a.com
    smtp:
      host: smtp.a.com
    auth:
      username: a
      password: p
    llm:
      api_key: sk-per-account
"""
    )
    with pytest.raises(ConfigurationError) as excinfo:
        MailAccountsConfig.from_yaml(yaml_file)
    message = str(excinfo.value)
    assert "personal" in message
    assert "llm" in message.lower()
    assert "top-level" in message.lower() or "outside" in message.lower()


def test_from_yaml_per_account_langfuse_rejected(tmp_path: Path) -> None:
    """Per-account langfuse: block raises ConfigurationError with actionable message."""
    yaml_file = tmp_path / "accts.yaml"
    yaml_file.write_text(
        """\
accounts:
  - id: work
    imap:
      host: imap.a.com
    smtp:
      host: smtp.a.com
    auth:
      username: a
      password: p
    langfuse:
      public_key: pk-per-account
"""
    )
    with pytest.raises(ConfigurationError) as excinfo:
        MailAccountsConfig.from_yaml(yaml_file)
    message = str(excinfo.value)
    assert "work" in message
    assert "langfuse" in message.lower()
    assert "top-level" in message.lower() or "outside" in message.lower()


# ---------------------------------------------------------------------------
# render_accounts_yaml top-level emission
# ---------------------------------------------------------------------------


def test_render_accounts_yaml_emits_top_level_llm() -> None:
    """render_accounts_yaml emits a top-level llm: section, not per-account."""
    account = MailAccount(
        "alpha",
        _cfg(
            llm_api_key="sk-test",
            llm_provider_model="",
            db_path=".data/alpha/mail.db",
        ),
    )
    text = render_accounts_yaml([account], "alpha")
    # Top-level llm: appears before accounts:
    llm_pos = text.index("llm:")
    accts_pos = text.index("accounts:")
    assert llm_pos < accts_pos, "llm: must appear before accounts:"
    assert "api_key: " in text
    assert "sk-test" in text
    # No per-account llm: block inside the account rendering
    # (the llm: line appears exactly once — the top-level one)
    assert text.count("llm:") == 1


def test_render_accounts_yaml_emits_top_level_langfuse() -> None:
    """render_accounts_yaml emits a top-level langfuse: section, not per-account."""
    account = MailAccount(
        "alpha",
        _cfg(
            langfuse_public_key="pk-lf-test",
            langfuse_secret_key="sk-lf-test",
            langfuse_base_url="https://cloud.langfuse.com",
            db_path=".data/alpha/mail.db",
        ),
    )
    text = render_accounts_yaml([account], "alpha")
    langfuse_pos = text.index("langfuse:")
    accts_pos = text.index("accounts:")
    assert langfuse_pos < accts_pos, "langfuse: must appear before accounts:"
    assert "public_key: " in text
    assert "pk-lf-test" in text
    assert "secret_key: " in text
    assert "sk-lf-test" in text
    assert text.count("langfuse:") == 1


def test_render_accounts_yaml_omits_llm_when_defaults() -> None:
    """render_accounts_yaml does NOT emit llm: when api_key is empty and
    provider is the default."""
    account = MailAccount("alpha", _cfg(db_path=".data/alpha/mail.db"))
    text = render_accounts_yaml([account], "alpha")
    assert "llm:" not in text


def test_render_accounts_yaml_omits_langfuse_when_all_empty() -> None:
    """render_accounts_yaml does NOT emit langfuse: when all fields are empty."""
    account = MailAccount("alpha", _cfg(db_path=".data/alpha/mail.db"))
    text = render_accounts_yaml([account], "alpha")
    assert "langfuse:" not in text


def test_render_accounts_yaml_emits_llm_when_only_provider_non_default() -> None:
    """llm: section emitted even with empty api_key if provider differs from default."""
    account = MailAccount(
        "alpha",
        _cfg(
            llm_provider_model="claude-sdk",
            db_path=".data/alpha/mail.db",
        ),
    )
    text = render_accounts_yaml([account], "alpha")
    assert "llm:" in text
    assert 'provider_model: "claude-sdk"' in text
    assert "api_key:" not in text  # empty api_key not emitted


# ---------------------------------------------------------------------------
# Multi-account from_env with bare global vars
# ---------------------------------------------------------------------------


def test_from_env_multi_account_bare_llm_vars() -> None:
    """Bare LLM_API_KEY / LLM_PROVIDER_MODEL populate global fields in multi-account env."""
    env = {
        "LLM_API_KEY": "sk-bare",
        "LLM_PROVIDER_MODEL": "claude-sdk",
        "MAIL_ACCOUNTS_0_ID": "a",
        "MAIL_ACCOUNTS_0_IMAP_HOST": "imap.a.com",
        "MAIL_ACCOUNTS_0_SMTP_HOST": "smtp.a.com",
        "MAIL_ACCOUNTS_0_USERNAME": "a",
        "MAIL_ACCOUNTS_0_PASSWORD": "p",
        "MAIL_ACCOUNTS_1_ID": "b",
        "MAIL_ACCOUNTS_1_IMAP_HOST": "imap.b.com",
        "MAIL_ACCOUNTS_1_SMTP_HOST": "smtp.b.com",
        "MAIL_ACCOUNTS_1_USERNAME": "b",
        "MAIL_ACCOUNTS_1_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        accounts = MailAccountsConfig.from_env()
    assert accounts.get("a").config.llm_api_key == "sk-bare"
    assert accounts.get("a").config.llm_provider_model == "claude-sdk"
    assert accounts.get("b").config.llm_api_key == "sk-bare"
    assert accounts.get("b").config.llm_provider_model == "claude-sdk"


def test_from_env_multi_account_bare_langfuse_vars() -> None:
    """Bare LANGFUSE_* vars populate global fields in multi-account env."""
    env = {
        "LANGFUSE_PUBLIC_KEY": "pk-bare",
        "LANGFUSE_SECRET_KEY": "sk-bare",
        "LANGFUSE_BASE_URL": "https://lf.example.com",
        "MAIL_ACCOUNTS_0_ID": "a",
        "MAIL_ACCOUNTS_0_IMAP_HOST": "imap.a.com",
        "MAIL_ACCOUNTS_0_SMTP_HOST": "smtp.a.com",
        "MAIL_ACCOUNTS_0_USERNAME": "a",
        "MAIL_ACCOUNTS_0_PASSWORD": "p",
        "MAIL_ACCOUNTS_1_ID": "b",
        "MAIL_ACCOUNTS_1_IMAP_HOST": "imap.b.com",
        "MAIL_ACCOUNTS_1_SMTP_HOST": "smtp.b.com",
        "MAIL_ACCOUNTS_1_USERNAME": "b",
        "MAIL_ACCOUNTS_1_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        accounts = MailAccountsConfig.from_env()
    cfg_a = accounts.get("a").config
    assert cfg_a.langfuse_public_key == "pk-bare"
    assert cfg_a.langfuse_secret_key == "sk-bare"
    assert cfg_a.langfuse_base_url == "https://lf.example.com"
    cfg_b = accounts.get("b").config
    assert cfg_b.langfuse_public_key == "pk-bare"
    assert cfg_b.langfuse_secret_key == "sk-bare"
    assert cfg_b.langfuse_base_url == "https://lf.example.com"


def test_from_env_multi_account_bare_wins_over_namespaced_llm() -> None:
    """Bare LLM_API_KEY overrides a namespaced MAIL_ACCOUNTS_0_LLM_API_KEY."""
    env = {
        "LLM_API_KEY": "sk-bare",
        "MAIL_ACCOUNTS_0_ID": "a",
        "MAIL_ACCOUNTS_0_LLM_API_KEY": "sk-namespaced",
        "MAIL_ACCOUNTS_0_IMAP_HOST": "imap.a.com",
        "MAIL_ACCOUNTS_0_SMTP_HOST": "smtp.a.com",
        "MAIL_ACCOUNTS_0_USERNAME": "a",
        "MAIL_ACCOUNTS_0_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        accounts = MailAccountsConfig.from_env()
    # Bare env var wins; the namespaced value is ignored.
    assert accounts.get("a").config.llm_api_key == "sk-bare"


def test_from_env_multi_account_bare_wins_over_namespaced_langfuse() -> None:
    """Bare LANGFUSE_PUBLIC_KEY overrides a namespaced MAIL_ACCOUNTS_0_LANGFUSE_PUBLIC_KEY."""
    env = {
        "LANGFUSE_PUBLIC_KEY": "pk-bare",
        "MAIL_ACCOUNTS_0_ID": "a",
        "MAIL_ACCOUNTS_0_LANGFUSE_PUBLIC_KEY": "pk-namespaced",
        "MAIL_ACCOUNTS_0_IMAP_HOST": "imap.a.com",
        "MAIL_ACCOUNTS_0_SMTP_HOST": "smtp.a.com",
        "MAIL_ACCOUNTS_0_USERNAME": "a",
        "MAIL_ACCOUNTS_0_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        accounts = MailAccountsConfig.from_env()
    # Bare env var wins; the namespaced value is ignored.
    assert accounts.get("a").config.langfuse_public_key == "pk-bare"


def test_from_env_multi_account_global_fields_default_when_not_set() -> None:
    """When bare global env vars are absent, global fields fall back to defaults."""
    env = {
        "MAIL_ACCOUNTS_0_ID": "a",
        "MAIL_ACCOUNTS_0_IMAP_HOST": "imap.a.com",
        "MAIL_ACCOUNTS_0_SMTP_HOST": "smtp.a.com",
        "MAIL_ACCOUNTS_0_USERNAME": "a",
        "MAIL_ACCOUNTS_0_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        accounts = MailAccountsConfig.from_env()
    cfg = accounts.get("a").config
    assert cfg.llm_api_key == ""
    assert cfg.llm_provider_model == ""
    assert cfg.langfuse_public_key == ""
    assert cfg.langfuse_secret_key == ""
    assert cfg.langfuse_base_url == ""


# ---------------------------------------------------------------------------
# Failed-account resilience — one bad account must not take down the board
# ---------------------------------------------------------------------------


def test_from_yaml_skips_template_literal_account(tmp_path: Path) -> None:
    """A config with 3 accounts where account-1 (index 1) has an unsubstituted
    template literal must return 2 valid accounts and 1 failed entry."""
    yaml_content = textwrap.dedent("""\
        accounts:
          - id: good-1
            imap:
              host: imap.example.com
            smtp:
              host: smtp.example.com
            auth:
              username: alice@example.com
              password: secret
          - id: bad-2
            imap:
              host: imap.example.com
            smtp:
              host: smtp.example.com
            auth:
              username: "{accounts.2.auth.username}"
              password: secret
          - id: good-3
            imap:
              host: imap.example.com
            smtp:
              host: smtp.example.com
            auth:
              username: carol@example.com
              password: secret
    """)
    config_file = tmp_path / "mail.local.yaml"
    config_file.write_text(yaml_content)

    result = MailAccountsConfig.from_yaml(config_file)

    assert [a.account_id for a in result.accounts] == ["good-1", "good-3"]
    assert len(result.failed_accounts) == 1
    assert result.failed_accounts[0].account_id == "bad-2"
    assert "template literal" in result.failed_accounts[0].error


def test_from_yaml_all_template_literal_raises(tmp_path: Path) -> None:
    """When every account has a template literal, from_yaml() must raise."""
    yaml_content = textwrap.dedent("""\
        accounts:
          - id: bad-1
            imap:
              host: imap.example.com
            smtp:
              host: smtp.example.com
            auth:
              username: "{accounts.1.auth.username}"
              password: secret
    """)
    config_file = tmp_path / "mail.local.yaml"
    config_file.write_text(yaml_content)

    with pytest.raises(ConfigurationError, match="All accounts failed"):
        MailAccountsConfig.from_yaml(config_file)
