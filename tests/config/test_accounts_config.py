"""Tests for the multi-account configuration layer."""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.config import (
    ConfigurationError,
    MailAccount,
    MailAccountsConfig,
    MailConfig,
    load_accounts,
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
    accounts = MailAccountsConfig.from_yaml("config/mail.local.example.yaml")
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
    env = {"MAIL_CONFIG_PATH": "config/mail.local.example.yaml"}
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
