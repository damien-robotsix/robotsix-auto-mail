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

    accounts = _accounts(cfg)

    with (
        mock.patch(
            "robotsix_auto_mail.cli._ingest_cycle", return_value=0
        ) as mock_cycle,
        mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=accounts),
        mock.patch("robotsix_auto_mail.cli.time.sleep", side_effect=KeyboardInterrupt),
        mock.patch(
            "robotsix_auto_mail.cli.commands_ingest.probe_account",
            return_value=("ok", None),
        ),
    ):
        rc = _cmd_ingest(accounts, watch=True)

    assert rc == 0
    mock_cycle.assert_called()
    assert "Watch stopped" in capsys.readouterr().out


def test_ingest_watch_survives_cycle_error(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failing cycle is logged and does not abort the watch loop."""
    from robotsix_auto_mail.cli import _cmd_ingest

    accounts = _accounts(cfg)

    with (
        mock.patch(
            "robotsix_auto_mail.cli._ingest_cycle",
            side_effect=RuntimeError("boom"),
        ),
        mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=accounts),
        mock.patch("robotsix_auto_mail.cli.time.sleep", side_effect=KeyboardInterrupt),
        mock.patch(
            "robotsix_auto_mail.cli.commands_ingest.probe_account",
            return_value=("ok", None),
        ),
    ):
        rc = _cmd_ingest(accounts, watch=True)

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
    mock_cycle.assert_called()
    assert mock_cycle.call_args_list[0] == mock.call(cfg, dry_run=False)


def test_ingest_watch_heartbeat_file_touched(
    cfg: MailConfig, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With --heartbeat-file in watch mode, the file is touched after a cycle."""
    from robotsix_auto_mail.cli import _cmd_ingest

    hb = tmp_path / "test.heartbeat"
    assert not hb.exists()

    with (
        mock.patch("robotsix_auto_mail.cli._ingest_cycle", return_value=0),
        mock.patch("robotsix_auto_mail.cli.time.sleep", side_effect=KeyboardInterrupt),
    ):
        rc = _cmd_ingest(_accounts(cfg), watch=True, heartbeat_file=str(hb))

    assert rc == 0
    assert hb.exists()
    # mtime should be within the last few seconds
    import time as _time

    age_s = _time.time() - hb.stat().st_mtime
    assert age_s >= 0
    assert age_s < 10


def test_ingest_watch_heartbeat_file_omitted_no_file_written(
    cfg: MailConfig, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When --heartbeat-file is omitted, no file is created."""
    from robotsix_auto_mail.cli import _cmd_ingest

    hb = tmp_path / "should_not_exist.heartbeat"

    with (
        mock.patch("robotsix_auto_mail.cli._ingest_cycle", return_value=0),
        mock.patch("robotsix_auto_mail.cli.time.sleep", side_effect=KeyboardInterrupt),
    ):
        rc = _cmd_ingest(_accounts(cfg), watch=True, heartbeat_file=None)

    assert rc == 0
    assert not hb.exists()


# ---------------------------------------------------------------------------
# multi-account selection
# ---------------------------------------------------------------------------


def test_command_uses_first_account_when_multiple(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """_load_config_or_exit(None) exits with error listing available accounts."""
    import pytest

    from robotsix_auto_mail.cli import _load_config_or_exit

    accounts = _two_accounts(tmp_path)
    with mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=accounts):
        with pytest.raises(SystemExit) as exc_info:
            _load_config_or_exit(None)
        assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "--account is required" in captured.err
    assert "personal" in captured.err
    assert "work" in captured.err


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


# ---------------------------------------------------------------------------
# watch-mode idle heartbeat (zero / password-less accounts)
# ---------------------------------------------------------------------------


def test_ingest_watch_idle_heartbeat_zero_accounts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Watch mode with zero accounts enters idle loop and writes heartbeat."""
    from robotsix_auto_mail.cli import _cmd_ingest
    from robotsix_auto_mail.config import ConfigurationError

    hb = tmp_path / "idle.heartbeat"
    assert not hb.exists()

    with (
        mock.patch(
            "robotsix_auto_mail.cli.load_accounts",
            side_effect=ConfigurationError("accounts list must not be empty"),
        ),
        mock.patch(
            "robotsix_auto_mail.cli.time.sleep",
            side_effect=KeyboardInterrupt,
        ),
    ):
        rc = _cmd_ingest(None, watch=True, heartbeat_file=str(hb))

    assert rc == 0
    assert hb.exists()
    out = capsys.readouterr().out
    assert "idle: no watchable accounts configured; waiting" in out
    assert "Watch stopped" in out


def test_ingest_watch_idle_heartbeat_passwordless(
    cfg: MailConfig,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Watch mode with password-less accounts enters idle loop and writes heartbeat."""
    from robotsix_auto_mail.cli import _cmd_ingest
    from robotsix_auto_mail.config import ConfigurationError

    # Build an account with no password.
    pwless_cfg = MailConfig(
        imap_host=cfg.imap_host,
        smtp_host=cfg.smtp_host,
        username=cfg.username,
        password="",
    )
    accounts = _accounts(pwless_cfg)

    hb = tmp_path / "pwless.heartbeat"
    assert not hb.exists()

    with (
        mock.patch(
            "robotsix_auto_mail.cli.load_accounts",
            side_effect=ConfigurationError("accounts list must not be empty"),
        ),
        mock.patch(
            "robotsix_auto_mail.cli.time.sleep",
            side_effect=KeyboardInterrupt,
        ),
    ):
        rc = _cmd_ingest(accounts, watch=True, heartbeat_file=str(hb))

    assert rc == 0
    assert hb.exists()
    out = capsys.readouterr().out
    assert "idle: no watchable accounts configured; waiting" in out
    assert "Watch stopped" in out


def test_ingest_watch_transition_idle_to_active(
    cfg: MailConfig,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Idle loop transitions to active ingestion when accounts appear between cycles."""
    from robotsix_auto_mail.cli import _cmd_ingest
    from robotsix_auto_mail.config import ConfigurationError

    hb = tmp_path / "trans.heartbeat"
    # Give the account a real db_path so init_db can create the file.
    acct_cfg = MailConfig(
        imap_host=cfg.imap_host,
        smtp_host=cfg.smtp_host,
        username=cfg.username,
        password=cfg.password,
        db_path=str(tmp_path / "test.db"),
    )
    accounts = _accounts(acct_cfg)

    # load_accounts: first raise (stay idle), then return accounts (transition).
    load_calls: list[object] = [
        ConfigurationError("accounts list must not be empty"),
        accounts,
    ]

    # time.sleep: first call passes (idle loop), second raises (active loop).
    sleep_calls: list[object] = [None, KeyboardInterrupt]

    with (
        mock.patch(
            "robotsix_auto_mail.cli.load_accounts",
            side_effect=load_calls,
        ) as mock_load,
        mock.patch(
            "robotsix_auto_mail.cli.commands_ingest.probe_account",
            return_value=("ok", None),
        ),
        mock.patch(
            "robotsix_auto_mail.cli._ingest_cycle",
            return_value=0,
        ) as mock_cycle,
        mock.patch(
            "robotsix_auto_mail.cli.time.sleep",
            side_effect=sleep_calls,
        ),
    ):
        rc = _cmd_ingest(None, watch=True, heartbeat_file=str(hb))

    assert rc == 0
    # load_accounts called at least twice (idle + transition + active loop reload).
    assert mock_load.call_count >= 2
    # After transition, the active watch loop runs _ingest_cycle.
    mock_cycle.assert_called()
    assert hb.exists()
    out = capsys.readouterr().out
    assert "idle: no watchable accounts configured; waiting" in out
    assert "STARTUP: account" in out


def test_ingest_non_watch_zero_accounts_returns_0(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-watch ingest with accounts=None prints a message and returns 0."""
    from robotsix_auto_mail.cli import _cmd_ingest

    rc = _cmd_ingest(None, watch=False)
    assert rc == 0
    out = capsys.readouterr().err
    assert "No accounts configured" in out


def test_ingest_non_watch_passwordless_returns_0(
    cfg: MailConfig,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One-shot ingest with password-less accounts prints a message and returns 0."""
    from robotsix_auto_mail.cli import _cmd_ingest

    pwless_cfg = MailConfig(
        imap_host=cfg.imap_host,
        smtp_host=cfg.smtp_host,
        username=cfg.username,
        password="",
    )
    accounts = _accounts(pwless_cfg)

    rc = _cmd_ingest(accounts, watch=False)
    assert rc == 0
    out = capsys.readouterr().err
    assert "No accounts have passwords configured" in out


def test_ingest_non_watch_empty_config_exits_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One-shot ingest CLI with empty config exits 1 (via _load_accounts_or_exit)."""
    from robotsix_auto_mail.config import ConfigurationError

    with mock.patch(
        "robotsix_auto_mail.cli.load_accounts",
        side_effect=ConfigurationError("accounts list must not be empty"),
    ):
        with pytest.raises(SystemExit) as exc:
            main(["ingest"])

    assert exc.value.code == 1
    assert "accounts list must not be empty" in capsys.readouterr().err
