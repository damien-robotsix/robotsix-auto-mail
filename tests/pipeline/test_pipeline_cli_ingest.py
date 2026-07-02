"""Tests for the CLI ingest subcommand."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.db import init_db
from robotsix_auto_mail.imap import ImapClient, ImapError
from robotsix_auto_mail.pipeline import IngestError, IngestResult

# ---------------------------------------------------------------------------
# CLI ingest subcommand tests
# ---------------------------------------------------------------------------


def test_cli_ingest_subcommand_in_parser() -> None:
    """build_parser includes the ingest subcommand."""
    from robotsix_auto_mail.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["ingest"])
    assert args.command == "ingest"


def test_cli_ingest_rejects_extra_args() -> None:
    """ingest rejects extra arguments."""
    from robotsix_auto_mail.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["ingest", "--foo"])


@pytest.fixture
def env_cfg_ingest() -> MailConfig:
    return MailConfig(
        imap_host="imap.example.com",
        imap_port=993,
        imap_tls_mode="direct-tls",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_tls_mode="starttls",
        username="user@example.com",
        password="s3cret",
        db_path=":memory:",
    )


def _accounts_ingest(cfg: MailConfig) -> MailAccountsConfig:
    """Wrap a single ``MailConfig`` in a one-element accounts container."""
    return MailAccountsConfig(
        accounts=(MailAccount(account_id="default", config=cfg, label=None),),
        default_account_id="default",
    )


@mock.patch("robotsix_auto_mail.cli.ImapClient")
@mock.patch("robotsix_auto_mail.cli.init_db")
@mock.patch(
    "robotsix_auto_mail.cli.load_accounts",
)
def test_cli_ingest_with_errors_exits_zero(
    mock_load_accounts: mock.MagicMock,
    mock_init_db: mock.MagicMock,
    mock_imap_cls: mock.MagicMock,
    env_cfg_ingest: MailConfig,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ingest subcommand exits 0 even when per-message errors are present."""
    mock_load_accounts.return_value = _accounts_ingest(env_cfg_ingest)

    # Set up an in-memory DB for init_db.
    db = init_db(":memory:")
    mock_init_db.return_value = db

    # Mock ImapClient context manager.
    mock_imap = mock.MagicMock(spec=ImapClient)
    mock_imap_cls.return_value.__enter__.return_value = mock_imap

    # Mock ingest_mail return.
    with mock.patch("robotsix_auto_mail.cli.ingest_mail") as mock_ingest:
        mock_ingest.return_value = IngestResult(
            total_fetched=12,
            stored=10,
            skipped=1,
            errors=[
                IngestError(
                    uid=42,
                    message_id="<msg-id@example.com>",
                    error="failed to parse raw bytes as MIME message",
                ),
            ],
            triaged=4,
        )

        from robotsix_auto_mail.cli import main

        rc = main(["ingest"])

    db.close()

    # Per-message errors are non-fatal; pipeline ran fine.
    assert rc == 0

    captured = capsys.readouterr()
    out = captured.out

    assert "Fetched: 12 messages" in out
    assert "Stored:  10 new" in out
    assert "Skipped:  1 duplicate" in out
    assert "Triaged:  4" in out
    assert "Errors:   1" in out
    assert "UID 42 (<msg-id@example.com>)" in out
    assert "failed to parse raw bytes as MIME message" in out


@mock.patch("robotsix_auto_mail.cli.ImapClient")
@mock.patch("robotsix_auto_mail.cli.init_db")
@mock.patch(
    "robotsix_auto_mail.cli.load_accounts",
)
def test_cli_ingest_success_no_errors(
    mock_load_accounts: mock.MagicMock,
    mock_init_db: mock.MagicMock,
    mock_imap_cls: mock.MagicMock,
    env_cfg_ingest: MailConfig,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ingest subcommand exits 0 when there are no errors."""
    mock_load_accounts.return_value = _accounts_ingest(env_cfg_ingest)

    db = init_db(":memory:")
    mock_init_db.return_value = db

    mock_imap = mock.MagicMock(spec=ImapClient)
    mock_imap_cls.return_value.__enter__.return_value = mock_imap

    with mock.patch("robotsix_auto_mail.cli.ingest_mail") as mock_ingest:
        mock_ingest.return_value = IngestResult(
            total_fetched=5,
            stored=5,
            skipped=0,
            errors=[],
        )

        from robotsix_auto_mail.cli import main

        rc = main(["ingest"])

    db.close()

    assert rc == 0
    captured = capsys.readouterr()
    out = captured.out
    assert "Fetched:  5 messages" in out
    assert "Stored:   5 new" in out
    assert "Errors:   0" in out


@mock.patch("robotsix_auto_mail.cli.ImapClient")
@mock.patch("robotsix_auto_mail.cli.init_db")
@mock.patch(
    "robotsix_auto_mail.cli.load_accounts",
)
def test_cli_ingest_imap_client_raises_exits_one(
    mock_load_accounts: mock.MagicMock,
    mock_init_db: mock.MagicMock,
    mock_imap_cls: mock.MagicMock,
    env_cfg_ingest: MailConfig,
) -> None:
    """ingest returns 1 when ImapClient raises (fatal connection failure)."""

    mock_load_accounts.return_value = _accounts_ingest(env_cfg_ingest)

    db = init_db(":memory:")
    mock_init_db.return_value = db

    mock_imap_cls.side_effect = ImapError("connection refused")

    from robotsix_auto_mail.cli import main

    rc = main(["ingest"])

    db.close()

    assert rc == 1


@mock.patch("robotsix_auto_mail.cli.ImapClient")
@mock.patch("robotsix_auto_mail.cli.init_db")
@mock.patch(
    "robotsix_auto_mail.cli.load_accounts",
)
def test_cli_ingest_dry_run_passes_flag(
    mock_load_accounts: mock.MagicMock,
    mock_init_db: mock.MagicMock,
    mock_imap_cls: mock.MagicMock,
    env_cfg_ingest: MailConfig,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ingest --dry-run passes dry_run=True to ingest_mail and prints banner."""
    mock_load_accounts.return_value = _accounts_ingest(env_cfg_ingest)

    db = init_db(":memory:")
    mock_init_db.return_value = db

    mock_imap = mock.MagicMock(spec=ImapClient)
    mock_imap_cls.return_value.__enter__.return_value = mock_imap

    with mock.patch("robotsix_auto_mail.cli.ingest_mail") as mock_ingest:
        mock_ingest.return_value = IngestResult(
            total_fetched=3,
            stored=3,
            skipped=0,
            errors=[],
        )

        from robotsix_auto_mail.cli import main

        rc = main(["ingest", "--dry-run"])

    db.close()

    # Verify ingest_mail was called with dry_run=True.
    assert mock_ingest.call_count == 1
    call_kwargs = mock_ingest.call_args.kwargs
    assert call_kwargs.get("dry_run") is True

    assert rc == 0

    captured = capsys.readouterr()
    out = captured.out
    assert "DRY RUN — nothing stored" in out
    assert "Fetched:  3 messages" in out
    assert "Stored:   3 new" in out


def test_parser_ingest_has_dry_run_flag() -> None:
    """--dry-run is accepted on the ingest subparser."""
    from robotsix_auto_mail.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["ingest", "--dry-run"])
    assert args.dry_run is True

    args2 = parser.parse_args(["ingest"])
    assert args2.dry_run is False


@mock.patch("robotsix_auto_mail.cli.load_accounts")
def test_cli_ingest_config_load_failure(
    mock_load_accounts: mock.MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ingest exits with code 1 when config loading fails."""
    mock_load_accounts.side_effect = RuntimeError("boom")

    from robotsix_auto_mail.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["ingest"])

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Error loading configuration" in err
    assert "boom" in err
