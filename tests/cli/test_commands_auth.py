"""Unit tests for ``robotsix_auto_mail.cli.commands_auth._cmd_auth_login``."""

from __future__ import annotations

import argparse
import io
from pathlib import Path
from unittest import mock

from robotsix_auto_mail.cli.commands_auth import _cmd_auth_login
from robotsix_auto_mail.config import (
    ConfigurationError,
    MailAccount,
    MailAccountsConfig,
    MailConfig,
)


def _ms_accounts(tmp_path: Path) -> MailAccountsConfig:
    """Build a one-account container whose account uses the microsoft provider."""
    config = MailConfig(
        imap_host="outlook.office365.com",
        smtp_host="smtp.office365.com",
        username="me@contoso.com",
        password="",
        oauth2_provider="microsoft",
        db_path=str(tmp_path / "ms" / "mail.db"),
    )
    return MailAccountsConfig(
        accounts=(MailAccount(account_id="ms", config=config, label=None),),
        default_account_id="ms",
    )


def _two_accounts(tmp_path: Path) -> MailAccountsConfig:
    """Build a two-account container (``personal`` + ``work``)."""
    personal = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="me@example.com",
        password="s3cret",
        db_path=str(tmp_path / "personal.db"),
    )
    work = MailConfig(
        imap_host="imap.work.com",
        smtp_host="smtp.work.com",
        username="me@work.com",
        password="s3cret",
        db_path=str(tmp_path / "work.db"),
    )
    return MailAccountsConfig(
        accounts=(
            MailAccount(account_id="personal", config=personal, label=None),
            MailAccount(account_id="work", config=work, label=None),
        ),
        default_account_id="personal",
    )


# ---------------------------------------------------------------------------
# Successful login
# ---------------------------------------------------------------------------


def test_cmd_auth_login_success(tmp_path: Path) -> None:
    """Single OAuth2 account: device-code flow runs and cache path is printed."""
    accounts = _ms_accounts(tmp_path)
    args = argparse.Namespace(account=None)
    stdout = io.StringIO()
    stderr = io.StringIO()

    with (
        mock.patch(
            "robotsix_auto_mail.cli.commands_auth._load_accounts_or_exit",
            return_value=accounts,
        ),
        mock.patch("robotsix_auto_mail.oauth2.device_code_login") as mock_login,
        mock.patch("sys.stdout", stdout),
        mock.patch("sys.stderr", stderr),
    ):
        rc = _cmd_auth_login(args)

    assert rc == 0
    mock_login.assert_called_once_with(accounts.get("ms").config)
    assert "msal_cache.json" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_cmd_auth_login_with_explicit_account(tmp_path: Path) -> None:
    """Explicit --account flag on a single-account config still works."""
    accounts = _ms_accounts(tmp_path)
    args = argparse.Namespace(account="ms")
    stdout = io.StringIO()
    stderr = io.StringIO()

    with (
        mock.patch(
            "robotsix_auto_mail.cli.commands_auth._load_accounts_or_exit",
            return_value=accounts,
        ),
        mock.patch("robotsix_auto_mail.oauth2.device_code_login") as mock_login,
        mock.patch("sys.stdout", stdout),
        mock.patch("sys.stderr", stderr),
    ):
        rc = _cmd_auth_login(args)

    assert rc == 0
    mock_login.assert_called_once_with(accounts.get("ms").config)
    assert "msal_cache.json" in stdout.getvalue()


# ---------------------------------------------------------------------------
# Unknown account
# ---------------------------------------------------------------------------


def test_cmd_auth_login_unknown_account(tmp_path: Path) -> None:
    """An unknown --account id writes an error to stderr and returns 1."""
    accounts = _ms_accounts(tmp_path)
    args = argparse.Namespace(account="nope")
    stdout = io.StringIO()
    stderr = io.StringIO()

    with (
        mock.patch(
            "robotsix_auto_mail.cli.commands_auth._load_accounts_or_exit",
            return_value=accounts,
        ),
        mock.patch("sys.stdout", stdout),
        mock.patch("sys.stderr", stderr),
    ):
        rc = _cmd_auth_login(args)

    assert rc == 1
    assert "nope" in stderr.getvalue()
    assert stdout.getvalue() == ""


# ---------------------------------------------------------------------------
# Multiple accounts without --account flag
# ---------------------------------------------------------------------------


def test_cmd_auth_login_ambiguous_account(tmp_path: Path) -> None:
    """Omitting --account with multiple accounts returns 1 and lists ids."""
    accounts = _two_accounts(tmp_path)
    args = argparse.Namespace(account=None)
    stdout = io.StringIO()
    stderr = io.StringIO()

    with (
        mock.patch(
            "robotsix_auto_mail.cli.commands_auth._load_accounts_or_exit",
            return_value=accounts,
        ),
        mock.patch("sys.stdout", stdout),
        mock.patch("sys.stderr", stderr),
    ):
        rc = _cmd_auth_login(args)

    assert rc == 1
    err = stderr.getvalue()
    assert "personal" in err
    assert "work" in err
    assert stdout.getvalue() == ""


# ---------------------------------------------------------------------------
# Non-OAuth2 account
# ---------------------------------------------------------------------------


def test_cmd_auth_login_non_oauth2_account(tmp_path: Path) -> None:
    """A non-OAuth2 account is rejected with a clear message and return 1."""
    accounts = _two_accounts(tmp_path)
    args = argparse.Namespace(account="work")
    stdout = io.StringIO()
    stderr = io.StringIO()

    with (
        mock.patch(
            "robotsix_auto_mail.cli.commands_auth._load_accounts_or_exit",
            return_value=accounts,
        ),
        mock.patch("sys.stdout", stdout),
        mock.patch("sys.stderr", stderr),
    ):
        rc = _cmd_auth_login(args)

    assert rc == 1
    assert "OAuth2" in stderr.getvalue()
    assert stdout.getvalue() == ""


# ---------------------------------------------------------------------------
# Device-flow failure
# ---------------------------------------------------------------------------


def test_cmd_auth_login_device_flow_config_error(tmp_path: Path) -> None:
    """A ConfigurationError during device_code_login is caught and returned."""
    accounts = _ms_accounts(tmp_path)
    args = argparse.Namespace(account=None)
    stdout = io.StringIO()
    stderr = io.StringIO()

    with (
        mock.patch(
            "robotsix_auto_mail.cli.commands_auth._load_accounts_or_exit",
            return_value=accounts,
        ),
        mock.patch(
            "robotsix_auto_mail.oauth2.device_code_login",
            side_effect=ConfigurationError("msal not installed"),
        ),
        mock.patch("sys.stdout", stdout),
        mock.patch("sys.stderr", stderr),
    ):
        rc = _cmd_auth_login(args)

    assert rc == 1
    assert "msal not installed" in stderr.getvalue()


def test_cmd_auth_login_device_flow_generic_error(tmp_path: Path) -> None:
    """A generic Exception during device_code_login is caught and returned."""
    accounts = _ms_accounts(tmp_path)
    args = argparse.Namespace(account=None)
    stdout = io.StringIO()
    stderr = io.StringIO()

    with (
        mock.patch(
            "robotsix_auto_mail.cli.commands_auth._load_accounts_or_exit",
            return_value=accounts,
        ),
        mock.patch(
            "robotsix_auto_mail.oauth2.device_code_login",
            side_effect=RuntimeError("device flow aborted"),
        ),
        mock.patch("sys.stdout", stdout),
        mock.patch("sys.stderr", stderr),
    ):
        rc = _cmd_auth_login(args)

    assert rc == 1
    assert "device-code login failed" in stderr.getvalue()
    assert "device flow aborted" in stderr.getvalue()
