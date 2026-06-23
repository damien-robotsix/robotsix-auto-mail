"""Tests for the CLI config-sync subcommand."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.cli import build_parser, main
from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.config.config_sync_agent import (
    ConfigSyncError,
    ConfigSyncResult,
    DriftProposal,
)


def _accounts(cfg: MailConfig, account_id: str = "default") -> MailAccountsConfig:
    """Wrap a single ``MailConfig`` in a one-element accounts container."""
    return MailAccountsConfig(
        accounts=(MailAccount(account_id=account_id, config=cfg, label=None),),
        default_account_id=account_id,
    )


# ---------------------------------------------------------------------------
# config-sync subcommand
# ---------------------------------------------------------------------------


def _patch_config_sync_llm(
    result_obj: ConfigSyncResult,
) -> mock._patch[mock.MagicMock]:
    """Patch get_provider so the agent returns *result_obj*."""
    mock_run_result = mock.MagicMock()
    mock_run_result.output = result_obj
    mock_handle = mock.MagicMock()
    mock_handle.run_sync.return_value = mock_run_result

    mock_provider = mock.MagicMock()
    mock_provider.build_agent.return_value = mock_handle
    mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

    return mock.patch(
        "robotsix_llmio.core.get_provider_for_identifier",
        return_value=mock_provider,
    )


def test_parser_has_config_sync_subcommand() -> None:
    """The parser knows the config-sync subcommand with expected defaults."""
    args = build_parser().parse_args(["config-sync", "--output-format", "json"])
    assert args.command == "config-sync"
    assert args.output_format == "json"
    assert args.dedup is False
    assert args.api_key is None


def test_config_sync_text_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A result with proposals prints title + body to stdout and returns 0."""
    result = ConfigSyncResult(
        proposals=[
            DriftProposal(
                title="imap_folder default mismatch",
                body="Docs say INBOX.All but the dataclass default is INBOX.",
                affected_field="imap_folder",
                confidence="high",
            )
        ]
    )
    with (
        _patch_config_sync_llm(result),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["config-sync"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "imap_folder default mismatch" in out
    assert "Docs say INBOX.All but the dataclass default is INBOX." in out
    assert "imap_folder" in out


def test_config_sync_json_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--output-format json prints a parseable object and returns 0."""
    result = ConfigSyncResult(
        proposals=[
            DriftProposal(
                title="env key drift",
                body="The .env.example uses MAIL_USER but config expects USERNAME.",
                affected_field="username",
                confidence="medium",
            )
        ]
    )
    with (
        _patch_config_sync_llm(result),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["config-sync", "--output-format", "json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "proposals" in payload
    assert len(payload["proposals"]) == 1
    assert payload["proposals"][0]["title"] == "env key drift"
    assert payload["proposals"][0]["affected_field"] == "username"


def test_config_sync_no_drift(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty result prints the no-drift message and returns 0."""
    with (
        _patch_config_sync_llm(ConfigSyncResult(proposals=[])),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["config-sync"])

    assert rc == 0
    assert "No config drift detected." in capsys.readouterr().out


def test_config_sync_error_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A ConfigSyncError returns 1 and writes an Error: line to stderr."""
    with mock.patch(
        "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
        side_effect=ConfigSyncError("surface read failed"),
    ):
        rc = main(["config-sync"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Error:" in err
    assert "surface read failed" in err


def test_config_sync_api_key_precedence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--api-key overrides LLM_API_KEY env when constructing the provider."""
    with (
        _patch_config_sync_llm(ConfigSyncResult(proposals=[])) as cls,
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-env"}),
    ):
        rc = main(["config-sync", "--api-key", "sk-cli"])

    assert rc == 0
    cls.assert_called_once_with(identifier="openrouter-deepseek", api_key="sk-cli")


def test_config_sync_dedup_forwards_conn(
    tmp_path: Path,
) -> None:
    """--dedup forwards an open DB connection to the agent."""
    cfg_with_db = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        db_path=str(tmp_path / "ledger.db"),
    )
    with (
        mock.patch(
            "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
            return_value=ConfigSyncResult(proposals=[]),
        ) as mock_agent,
        mock.patch(
            "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_with_db)
        ),
    ):
        rc = main(["config-sync", "--dedup"])

    assert rc == 0
    assert mock_agent.call_args.kwargs["conn"] is not None


def test_parser_has_config_sync_set_subcommand() -> None:
    """The parser knows the config-sync-set subcommand with positional args."""
    args = build_parser().parse_args(["config-sync-set", "abc123", "accepted"])
    assert args.command == "config-sync-set"
    assert args.fingerprint == "abc123"
    assert args.state == "accepted"


def test_config_sync_set_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """config-sync-set transitions a known finding and exits 0."""
    from robotsix_auto_mail.config.config_sync_agent import (
        _load_ledger,
        _proposal_fingerprint,
        record_and_filter_proposals,
    )
    from robotsix_auto_mail.db import init_db as real_init_db

    cfg_db = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        db_path=str(tmp_path / "ledger.db"),
    )
    proposal = DriftProposal(
        title="imap_folder default mismatch",
        body="Docs say INBOX.All but the dataclass default is INBOX.",
        affected_field="imap_folder",
        confidence="high",
    )
    fingerprint = _proposal_fingerprint(proposal)
    conn = real_init_db(cfg_db.db_path)
    try:
        record_and_filter_proposals(conn, [proposal])
    finally:
        conn.close()

    with mock.patch(
        "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
    ):
        rc = main(["config-sync-set", fingerprint, "accepted"])

    assert rc == 0
    assert "Recorded config-drift finding state" in capsys.readouterr().out

    conn = real_init_db(cfg_db.db_path)
    try:
        ledger = _load_ledger(conn)
        assert ledger[fingerprint].state == "accepted"
    finally:
        conn.close()


def test_config_sync_set_invalid_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """config-sync-set exits 1 with a clear message on an invalid state."""
    cfg_db = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        db_path=str(tmp_path / "ledger.db"),
    )
    with mock.patch(
        "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
    ):
        rc = main(["config-sync-set", "abc123", "banana"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "invalid state" in err
    assert "banana" in err


def test_config_sync_set_unknown_fingerprint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """config-sync-set exits 1 when the fingerprint is unknown."""
    cfg_db = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        db_path=str(tmp_path / "ledger.db"),
    )
    with mock.patch(
        "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
    ):
        rc = main(["config-sync-set", "deadbeef", "accepted"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "No ledger finding" in err
    assert "deadbeef" in err
