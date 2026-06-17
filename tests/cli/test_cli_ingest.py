"""Tests for the CLI ingest subcommand and multi-account selection."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.cli import build_parser, main
from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _accounts(cfg: MailConfig, account_id: str = "default") -> MailAccountsConfig:
    """Wrap a single ``MailConfig`` in a one-element accounts container."""
    return MailAccountsConfig(
        accounts=(MailAccount(account_id=account_id, config=cfg, label=None),),
        default_account_id=account_id,
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
# ingest --watch
# ---------------------------------------------------------------------------


def test_ingest_watch_parser() -> None:
    """The ingest subcommand exposes --watch (default False)."""
    parser = build_parser()
    assert parser.parse_args(["ingest", "--watch"]).watch is True
    assert parser.parse_args(["ingest"]).watch is False


def test_ingest_watch_loops_then_stops_on_interrupt(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """Watch mode runs a cycle, then exits 0 when interrupted during sleep."""
    from robotsix_auto_mail.cli import _cmd_ingest

    with (
        mock.patch(
            "robotsix_auto_mail.cli._ingest_cycle", return_value=0
        ) as mock_cycle,
        mock.patch("robotsix_auto_mail.cli.time.sleep", side_effect=KeyboardInterrupt),
    ):
        rc = _cmd_ingest(_accounts(cfg), watch=True)

    assert rc == 0
    mock_cycle.assert_called_once()
    assert "Watch stopped" in capsys.readouterr().out


def test_ingest_watch_survives_cycle_error(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failing cycle is logged and does not abort the watch loop."""
    from robotsix_auto_mail.cli import _cmd_ingest

    with (
        mock.patch(
            "robotsix_auto_mail.cli._ingest_cycle",
            side_effect=RuntimeError("boom"),
        ),
        mock.patch("robotsix_auto_mail.cli.time.sleep", side_effect=KeyboardInterrupt),
    ):
        rc = _cmd_ingest(_accounts(cfg), watch=True)

    assert rc == 0
    assert "Ingest cycle failed" in capsys.readouterr().err


def test_ingest_single_pass_unaffected(
    cfg: MailConfig,
) -> None:
    """Without --watch, _cmd_ingest delegates to a single cycle."""
    from robotsix_auto_mail.cli import _cmd_ingest

    with mock.patch(
        "robotsix_auto_mail.cli._ingest_cycle", return_value=0
    ) as mock_cycle:
        rc = _cmd_ingest(_accounts(cfg), watch=False)

    assert rc == 0
    mock_cycle.assert_called_once_with(cfg, dry_run=False)


# ---------------------------------------------------------------------------
# multi-account selection
# ---------------------------------------------------------------------------


def test_command_defaults_to_default_account_when_multiple(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A command with multiple accounts and no --account uses the default."""
    from robotsix_auto_mail.cli import _load_config_or_exit

    accounts = _two_accounts(tmp_path)
    with mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=accounts):
        config = _load_config_or_exit(None)

    assert config is accounts.default.config


def test_ingest_all_accounts_runs_each_cycle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ingest with no --account runs one cycle per account with a header each."""
    accounts = _two_accounts(tmp_path)
    with (
        mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=accounts),
        mock.patch(
            "robotsix_auto_mail.cli._ingest_cycle", return_value=0
        ) as mock_cycle,
    ):
        rc = main(["ingest"])

    assert rc == 0
    assert mock_cycle.call_count == 2
    configs = [call.args[0] for call in mock_cycle.call_args_list]
    assert accounts.get("personal").config in configs
    assert accounts.get("work").config in configs
    out = capsys.readouterr().out
    assert "=== account: personal ===" in out
    assert "=== account: work ===" in out


def test_ingest_all_accounts_flag_runs_each_cycle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ingest --all-accounts runs one cycle per account."""
    accounts = _two_accounts(tmp_path)
    with (
        mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=accounts),
        mock.patch(
            "robotsix_auto_mail.cli._ingest_cycle", return_value=0
        ) as mock_cycle,
    ):
        rc = main(["ingest", "--all-accounts"])

    assert rc == 0
    assert mock_cycle.call_count == 2


def test_ingest_selects_single_account(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ingest --account work runs a cycle for only the work account."""
    accounts = _two_accounts(tmp_path)
    with (
        mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=accounts),
        mock.patch(
            "robotsix_auto_mail.cli._ingest_cycle", return_value=0
        ) as mock_cycle,
    ):
        rc = main(["ingest", "--account", "work"])

    assert rc == 0
    mock_cycle.assert_called_once_with(accounts.get("work").config, dry_run=False)
    assert "=== account:" not in capsys.readouterr().out


def test_ingest_account_and_all_accounts_mutually_exclusive() -> None:
    """Passing both --account and --all-accounts fails with argparse exit 2."""
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["ingest", "--account", "a", "--all-accounts"])
    assert exc.value.code == 2
