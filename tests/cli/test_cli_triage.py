"""Tests for the CLI triage subcommand and triage state management."""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.cli import build_parser, main
from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.db import init_db, set_watermark
from robotsix_auto_mail.triage import (
    TriageError,
    TriageItem,
    TriageResult,
)


def _accounts(cfg: MailConfig, account_id: str = "default") -> MailAccountsConfig:
    """Wrap a single ``MailConfig`` in a one-element accounts container."""
    return MailAccountsConfig(
        accounts=(MailAccount(account_id=account_id, config=cfg, label=None),),
        default_account_id=account_id,
    )


# ---------------------------------------------------------------------------
# triage subcommand
# ---------------------------------------------------------------------------


def _patch_triage_llm(
    result_obj: TriageResult,
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
        "robotsix_llmio.core.get_provider",
        return_value=mock_provider,
    )


def _cfg_with_inbox(tmp_path: Path, message_id: str = "<a@x.com>") -> MailConfig:
    """A MailConfig pointing at a temp DB seeded with one inbox record."""
    from robotsix_auto_mail.db import (
        MailRecord,
        insert_record,
    )
    from robotsix_auto_mail.db import (
        init_db as real_init_db,
    )

    db_path = str(tmp_path / "triage.db")
    conn = real_init_db(db_path)
    insert_record(
        conn,
        MailRecord(
            message_id=message_id,
            sender="alice@example.com",
            subject="Hello",
            date="2025-06-01T12:00:00",
            body_plain="Just checking in!",
        ),
    )
    conn.close()
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        db_path=db_path,
    )


def test_parser_has_triage_subcommand() -> None:
    """The parser knows the triage subcommand with expected defaults."""
    args = build_parser().parse_args(["triage", "--output-format", "json"])
    assert args.command == "triage"
    assert args.output_format == "json"
    assert args.api_key is None


def test_parser_has_triage_set_subcommand() -> None:
    """The parser knows the triage-set subcommand with positional args."""
    args = build_parser().parse_args(["triage-set", "<a@x.com>", "TO_ANSWER"])
    assert args.command == "triage-set"
    assert args.message_id == "<a@x.com>"
    assert args.action == "TO_ANSWER"


def test_triage_text_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """triage prints decisions and exits 0 (text)."""
    cfg_db = _cfg_with_inbox(tmp_path)
    result = TriageResult(
        items=[TriageItem(index=1, action="TO_ANSWER", reason="needs reply")]
    )
    with (
        _patch_triage_llm(result),
        mock.patch(
            "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["triage"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "Inbox Triage" in out
    assert "<a@x.com>" in out
    assert "TO_ANSWER" in out
    assert "needs reply" in out


def test_triage_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """triage --output-format json prints a parseable list and exits 0."""
    cfg_db = _cfg_with_inbox(tmp_path)
    result = TriageResult(
        items=[TriageItem(index=1, action="TO_ARCHIVE", confidence="high")]
    )
    with (
        _patch_triage_llm(result),
        mock.patch(
            "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["triage", "--output-format", "json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert payload[0]["message_id"] == "<a@x.com>"
    assert payload[0]["action"] == "TO_ARCHIVE"
    assert payload[0]["source"] == "agent"


def test_triage_empty_inbox(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """triage prints a friendly message when there is no inbox mail."""
    cfg_db = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        db_path=str(tmp_path / "empty.db"),
    )
    with (
        mock.patch("robotsix_llmio.core.get_provider") as cls,
        mock.patch(
            "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["triage"])

    assert rc == 0
    assert "No inbox mail to triage." in capsys.readouterr().out
    cls.assert_not_called()


def test_triage_error_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A TriageError returns 1 and writes an Error: line to stderr."""
    cfg_db = _cfg_with_inbox(tmp_path)
    with (
        mock.patch(
            "robotsix_auto_mail.triage.run_triage_agent",
            side_effect=TriageError("llm exploded"),
        ),
        mock.patch(
            "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
        ),
    ):
        rc = main(["triage"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Error:" in err
    assert "llm exploded" in err


def test_triage_set_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """triage-set records a user decision and exits 0."""
    from robotsix_auto_mail.db import init_db as real_init_db
    from robotsix_auto_mail.triage import _load_memory, get_triage_decision

    cfg_db = _cfg_with_inbox(tmp_path)
    with mock.patch(
        "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
    ):
        rc = main(["triage-set", "<a@x.com>", "TO_ARCHIVE"])

    assert rc == 0
    assert "Recorded user triage decision" in capsys.readouterr().out

    conn = real_init_db(cfg_db.db_path)
    try:
        decision = get_triage_decision(conn, "<a@x.com>")
        assert decision is not None
        assert decision.action == "TO_ARCHIVE"
        assert decision.source == "user"
        # The user decision also updates the human-decision memory ledger.
        memory = _load_memory(conn)
        assert "alice@example.com" in memory
        assert memory["alice@example.com"].action == "TO_ARCHIVE"
    finally:
        conn.close()


def test_triage_set_invalid_action(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """triage-set exits 1 with a clear message on an invalid action."""
    cfg_db = _cfg_with_inbox(tmp_path)
    with mock.patch(
        "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
    ):
        rc = main(["triage-set", "<a@x.com>", "banana"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "invalid action" in err
    assert "banana" in err


def test_triage_set_unknown_message_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """triage-set exits 1 with a clear message when the message_id is unknown."""
    cfg_db = _cfg_with_inbox(tmp_path)
    with mock.patch(
        "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
    ):
        rc = main(["triage-set", "<missing@x.com>", "TO_ANSWER"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "no mail with message_id" in err
    assert "<missing@x.com>" in err


# ---------------------------------------------------------------------------
# _clear_stale_triage_state — boot-clear of orphaned triage_run:state
# ---------------------------------------------------------------------------


def test_clear_stale_triage_state_resets_running_flags(
    cfg: MailConfig, tmp_path: Path
) -> None:
    """A boot-clear resets every orphaned ``running`` flag to ``idle`` while
    leaving non-running flags untouched and tolerating a missing account DB."""
    from robotsix_auto_mail.cli.commands import _clear_stale_triage_state
    from robotsix_auto_mail.db import get_watermark

    # Account A: orphaned "running" flag (should be reset to "idle").
    db_a = str(tmp_path / "a" / "mail.db")
    conn_a = init_db(db_a)
    set_watermark(conn_a, "triage_run:state", "running")
    conn_a.close()

    # Account B: explicitly "idle" flag (should be left untouched).
    db_b = str(tmp_path / "b" / "mail.db")
    conn_b = init_db(db_b)
    set_watermark(conn_b, "triage_run:state", "idle")
    conn_b.close()

    # Account C: a bad DB path that cannot be opened — must not abort the loop.
    db_c = str(tmp_path / "missing-dir" / "nope" / "\x00bad" / "mail.db")

    accounts = MailAccountsConfig(
        accounts=(
            MailAccount(
                account_id="a",
                config=dataclasses.replace(cfg, db_path=db_a),
                label=None,
            ),
            MailAccount(
                account_id="c",
                config=dataclasses.replace(cfg, db_path=db_c),
                label=None,
            ),
            MailAccount(
                account_id="b",
                config=dataclasses.replace(cfg, db_path=db_b),
                label=None,
            ),
        ),
        default_account_id="a",
    )

    # Must not raise even though account C's DB cannot be opened.
    _clear_stale_triage_state(accounts)

    conn_a = init_db(db_a, skip_migrations=True)
    try:
        assert get_watermark(conn_a, "triage_run:state") == "idle"
    finally:
        conn_a.close()

    conn_b = init_db(db_b, skip_migrations=True)
    try:
        assert get_watermark(conn_b, "triage_run:state") == "idle"
    finally:
        conn_b.close()


def test_clear_stale_triage_state_resets_stale_batch_op(
    cfg: MailConfig, tmp_path: Path
) -> None:
    """A boot-clear also resets a stale ``batch_op:state`` (a non-idle JSON
    progress payload left by a SIGKILL'd batch worker) to ``"idle"`` for
    every configured account."""
    from robotsix_auto_mail.cli.commands import _clear_stale_triage_state
    from robotsix_auto_mail.db import get_watermark

    # Account A: orphaned running batch-delete progress payload.
    db_a = str(tmp_path / "a" / "mail.db")
    conn_a = init_db(db_a)
    set_watermark(
        conn_a,
        "batch_op:state",
        json.dumps({"op": "delete", "done": 3, "total": 9}),
    )
    conn_a.close()

    # Account B: already idle — left untouched.
    db_b = str(tmp_path / "b" / "mail.db")
    conn_b = init_db(db_b)
    set_watermark(conn_b, "batch_op:state", "idle")
    conn_b.close()

    accounts = MailAccountsConfig(
        accounts=(
            MailAccount(
                account_id="a",
                config=dataclasses.replace(cfg, db_path=db_a),
                label=None,
            ),
            MailAccount(
                account_id="b",
                config=dataclasses.replace(cfg, db_path=db_b),
                label=None,
            ),
        ),
        default_account_id="a",
    )

    _clear_stale_triage_state(accounts)

    conn_a = init_db(db_a, skip_migrations=True)
    try:
        assert get_watermark(conn_a, "batch_op:state") == "idle"
    finally:
        conn_a.close()

    conn_b = init_db(db_b, skip_migrations=True)
    try:
        assert get_watermark(conn_b, "batch_op:state") == "idle"
    finally:
        conn_b.close()
