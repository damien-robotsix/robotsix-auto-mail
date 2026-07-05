"""Tests for the CLI module."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.cli import build_parser, main
from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.imap import ImapClient
from robotsix_auto_mail.smtp import SmtpClient
from tests.conftest import _make_mock_imap_ssl, _make_mock_smtp


def _accounts(cfg: MailConfig, account_id: str = "default") -> MailAccountsConfig:
    """Wrap a single ``MailConfig`` in a one-element accounts container."""
    return MailAccountsConfig(
        accounts=(MailAccount(account_id=account_id, config=cfg, label=None),),
        default_account_id=account_id,
    )


# ---------------------------------------------------------------------------
# ImapClient / SmtpClient property defaults
# ---------------------------------------------------------------------------


def test_imap_client_properties_before_connect(cfg: MailConfig) -> None:
    """server_greeting / capabilities return safe defaults when not connected."""
    client = ImapClient(cfg)
    assert client.server_greeting is None
    assert client.capabilities == ()


def test_smtp_client_properties_before_connect(cfg: MailConfig) -> None:
    """ehlo_response / esmtp_features return safe defaults when not connected."""
    client = SmtpClient(cfg)
    assert client.ehlo_response is None
    assert client.esmtp_features == {}


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """--version prints the version and exits."""
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "0.0.0" in captured.out


# ---------------------------------------------------------------------------
# Parser shape
# ---------------------------------------------------------------------------


def test_parser_has_version() -> None:
    """The parser accepts --version."""
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0


def test_parser_has_probe_subcommand() -> None:
    """The parser knows the probe subcommand."""
    parser = build_parser()
    args = parser.parse_args(["probe"])
    assert args.command == "probe"


def test_probe_takes_no_extra_args() -> None:
    """probe rejects extra arguments."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["probe", "--foo"])


def test_no_subcommand_prints_help_and_exits_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Calling main() with no subcommand prints help to stderr and exits 1."""
    # Need to patch load() to avoid a real config call, but we want to
    # ensure we reach the dispatch.  With no command, we won't hit load().
    rc = main([])
    assert rc == 1
    # help goes to stderr
    captured = capsys.readouterr()
    assert "usage:" in captured.err.lower() or "usage:" in captured.out.lower()


# ---------------------------------------------------------------------------
# SmtpClient / ImapClient properties after connect
# ---------------------------------------------------------------------------


def test_smtp_client_properties_after_connect(cfg: MailConfig) -> None:
    """ehlo_response / esmtp_features reflect the mock after connect."""
    mock_smtp = _make_mock_smtp()

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        with SmtpClient(cfg) as client:
            assert client.ehlo_response == b"250-smtp.example.com\n250 STARTTLS"
            assert client.esmtp_features == {
                "STARTTLS": "",
                "AUTH": "PLAIN LOGIN",
            }


def test_imap_client_properties_after_connect(cfg: MailConfig) -> None:
    """server_greeting / capabilities reflect the mock after connect."""
    mock_imap = _make_mock_imap_ssl()

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap):
        with ImapClient(cfg) as client:
            assert client.server_greeting == b"* OK IMAP4 ready"
            assert client.capabilities == (
                "IMAP4rev1",
                "STARTTLS",
                "AUTH=PLAIN",
            )


# ---------------------------------------------------------------------------
# Multi-account selection (--account / --all-accounts)
# ---------------------------------------------------------------------------


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


@pytest.mark.parametrize(
    "command",
    [
        "probe",
        "ingest",
        "board",
        "serve",
        "triage",
        "triage-set",
        "config-sync",
        "config-sync-set",
    ],
)
def test_account_flag_accepted_by_subcommands(command: str) -> None:
    """Every account-consuming subcommand accepts ``--account ID``."""
    extra = {
        "triage-set": ["m@id", "INBOX"],
        "config-sync-set": ["fp", "accepted"],
    }.get(command, [])
    args = build_parser().parse_args([command, "--account", "work", *extra])
    assert args.account == "work"


def test_account_flag_help_documents_selection() -> None:
    """The --account help string documents account selection."""
    import argparse

    parser = build_parser()
    subparsers_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    board_parser = subparsers_action.choices["board"]
    action = next(a for a in board_parser._actions if a.dest == "account")
    assert action.help is not None
    assert "account" in action.help.lower()


def test_detect_rejects_account_flag() -> None:
    """detect does not load a mail config and rejects --account."""
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["detect", "me@example.com", "--account", "work"])
    assert exc.value.code == 2


def test_load_config_or_exit_selects_named_account(tmp_path: Path) -> None:
    """_load_config_or_exit('work') returns the work account's config."""
    from robotsix_auto_mail.cli import _load_config_or_exit

    accounts = _two_accounts(tmp_path)
    with mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=accounts):
        config = _load_config_or_exit("work")

    assert config is accounts.get("work").config


def test_load_config_or_exit_unknown_account(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """_load_config_or_exit('nope') errors with the valid ids and exits 1."""
    from robotsix_auto_mail.cli import _load_config_or_exit

    with mock.patch(
        "robotsix_auto_mail.cli.load_accounts", return_value=_two_accounts(tmp_path)
    ):
        with pytest.raises(SystemExit) as exc:
            _load_config_or_exit("nope")

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "nope" in err
    assert "personal" in err
    assert "work" in err


# ---------------------------------------------------------------------------
# auth login
# ---------------------------------------------------------------------------


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


def test_parser_has_auth_login_subcommand() -> None:
    """build_parser registers `auth login --account ID`."""
    args = build_parser().parse_args(["auth", "login", "--account", "ms"])
    assert args.command == "auth"
    assert args.auth_command == "login"
    assert args.account == "ms"


def test_auth_login_help_renders(capsys: pytest.CaptureFixture[str]) -> None:
    """`auth --help` and `auth login --help` render without error."""
    for argv in (["auth", "--help"], ["auth", "login", "--help"]):
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(argv)
        assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "login" in out


def test_auth_without_subcommand_prints_help_and_exits_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bare `auth` prints the auth help to stderr and exits non-zero."""
    rc = main(["auth"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "login" in err


def test_auth_login_single_account_runs_flow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With one account and no --account, the device-code flow runs and the
    cache location is printed."""
    accounts = _ms_accounts(tmp_path)
    with (
        mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=accounts),
        mock.patch("robotsix_auto_mail.oauth2.device_code_login") as mock_login,
    ):
        rc = main(["auth", "login"])

    assert rc == 0
    mock_login.assert_called_once_with(accounts.get("ms").config)
    out = capsys.readouterr().out
    assert "msal_cache.json" in out


def test_auth_login_ambiguous_account_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Omitting --account with multiple accounts errors and lists the ids."""
    accounts = _two_accounts(tmp_path)
    with mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=accounts):
        rc = main(["auth", "login"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "personal" in err
    assert "work" in err


def test_auth_login_unknown_account_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unknown --account id errors with the valid ids and exits non-zero."""
    accounts = _ms_accounts(tmp_path)
    with mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=accounts):
        rc = main(["auth", "login", "--account", "nope"])

    assert rc == 1
    assert "nope" in capsys.readouterr().err


def test_auth_login_non_microsoft_account_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-OAuth2 account is rejected with a clear message and non-zero exit."""
    accounts = _two_accounts(tmp_path)
    with mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=accounts):
        rc = main(["auth", "login", "--account", "work"])

    assert rc == 1
    assert "OAuth2" in capsys.readouterr().err


def test_auth_login_missing_msal_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing `msal` dependency yields the pip-install hint and non-zero exit."""
    from robotsix_auto_mail.config import ConfigurationError

    accounts = _ms_accounts(tmp_path)
    with (
        mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=accounts),
        mock.patch(
            "robotsix_auto_mail.oauth2.device_code_login",
            side_effect=ConfigurationError(
                "Microsoft OAuth2 (oauth2_provider='microsoft') requires the "
                "'msal' package, which is not installed. Install it with: "
                "pip install 'robotsix-auto-mail[microsoft]'"
            ),
        ),
    ):
        rc = main(["auth", "login", "--account", "ms"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "robotsix-auto-mail[microsoft]" in err
