"""Tests for load(), ConfigurationError, and JSON-file-only config loading.

Configuration is read exclusively from the JSON config file located by
``ROBOTSIX_CONFIG_FILE`` via the ``robotsix-config`` library.
"""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.config import (
    ConfigurationError,
    MailAccount,
    MailAccountsConfig,
    MailConfig,
    load,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_accounts() -> MailAccountsConfig:
    """Return a minimal single-account config."""
    return MailAccountsConfig(
        accounts=[
            MailAccount(
                account_id="default",
                config=MailConfig(
                    imap_host="imap.file.com",
                    smtp_host="smtp.file.com",
                    username="file_user",
                    password="file_pass",
                ),
            )
        ],
        default_account_id="default",
    )


# ---------------------------------------------------------------------------
# load() convenience function
# ---------------------------------------------------------------------------


def test_load_returns_default_account_config() -> None:
    """load() returns the default account's config from the config file."""
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        return_value=_default_accounts(),
    ):
        cfg = load()
    assert isinstance(cfg, MailConfig)
    assert cfg.imap_host == "imap.file.com"
    assert cfg.smtp_host == "smtp.file.com"
    assert cfg.username == "file_user"
    assert cfg.password == "file_pass"


def test_load_when_load_accounts_fails() -> None:
    """A failing load_accounts → ConfigurationError propagates."""
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        side_effect=ConfigurationError("no config"),
    ):
        with pytest.raises(ConfigurationError):
            load()


# ---------------------------------------------------------------------------
# ConfigurationError
# ---------------------------------------------------------------------------


def test_configuration_error_is_exception() -> None:
    """ConfigurationError is a proper Exception subclass."""
    err = ConfigurationError("test message")
    assert isinstance(err, Exception)
    assert str(err) == "test message"
    assert err.message == "test message"


def test_configuration_error_missing_only_default() -> None:
    """missing_only defaults to False."""
    err = ConfigurationError("test")
    assert err.missing_only is False


def test_configuration_error_missing_only_true() -> None:
    """missing_only can be set to True."""
    err = ConfigurationError("test", missing_only=True)
    assert err.missing_only is True


# ---------------------------------------------------------------------------
# MailConfig construction — password optional
# ---------------------------------------------------------------------------


def test_mailconfig_password_missing_ok() -> None:
    """A MailConfig without password loads with an empty password."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="",
    )
    assert cfg.password == ""
    assert cfg.username == "user@example.com"
