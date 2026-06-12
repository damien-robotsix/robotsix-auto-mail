"""Tests for the CLI module."""

from __future__ import annotations

import builtins
import dataclasses
import imaplib
import json
import os
import smtplib
import ssl
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from tests.conftest import _make_mock_imap_ssl, _make_mock_smtp

from robotsix_auto_mail.cli import _VerifyResult, build_parser, main
from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.config.config_sync_agent import (
    ConfigSyncError,
    ConfigSyncResult,
    DriftProposal,
)
from robotsix_auto_mail.detect import DetectionError, MailProvider
from robotsix_auto_mail.imap import ImapClient
from robotsix_auto_mail.smtp import SmtpClient
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
# probe - success
# ---------------------------------------------------------------------------


def test_probe_success(cfg: MailConfig, capsys: pytest.CaptureFixture[str]) -> None:
    """probe exits 0 and prints IMAP + SMTP metadata when both succeed."""
    mock_imap = _make_mock_imap_ssl()
    mock_smtp = _make_mock_smtp()

    with (
        mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 0
    captured = capsys.readouterr()
    out, err = captured.out, captured.err

    # IMAP output
    assert "IMAP Probe" in out
    assert "* OK IMAP4 ready" in out
    assert "IMAP4rev1" in out
    assert "INBOX" in out
    assert "[Gmail]" in out

    # SMTP output
    assert "SMTP Probe" in out
    assert "250-smtp.example.com" in out
    assert "STARTTLS" in out
    assert "AUTH" in out

    # No errors on stderr
    assert err == ""


# ---------------------------------------------------------------------------
# probe - IMAP failure, SMTP succeeds
# ---------------------------------------------------------------------------


def test_probe_imap_failure_smtp_ok(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """When IMAP fails, SMTP is still probed and exit code is 1."""
    mock_imap = mock.MagicMock(spec=imaplib.IMAP4_SSL)
    mock_imap.login.side_effect = imaplib.IMAP4.error("AUTHENTICATIONFAILED")
    mock_imap.sock = mock.MagicMock()

    mock_smtp = _make_mock_smtp()

    with (
        mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 1
    captured = capsys.readouterr()
    out, err = captured.out, captured.err

    # SMTP probe still ran
    assert "SMTP Probe" in out
    assert "250-smtp.example.com" in out

    # IMAP error on stderr
    assert "Error:" in err
    assert "AUTHENTICATIONFAILED" in err


# ---------------------------------------------------------------------------
# probe - SMTP failure, IMAP succeeds
# ---------------------------------------------------------------------------


def test_probe_smtp_failure_imap_ok(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """When SMTP fails, IMAP is still probed and exit code is 1."""
    mock_imap = _make_mock_imap_ssl()
    mock_smtp = mock.MagicMock(spec=smtplib.SMTP)
    mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(
        535, b"5.7.8 Authentication failed"
    )

    with (
        mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 1
    captured = capsys.readouterr()
    out, err = captured.out, captured.err

    # IMAP probe still ran
    assert "IMAP Probe" in out
    assert "INBOX" in out

    # SMTP error on stderr
    assert "Error:" in err
    assert "Authentication failed" in err


# ---------------------------------------------------------------------------
# probe - both fail
# ---------------------------------------------------------------------------


def test_probe_both_fail(cfg: MailConfig, capsys: pytest.CaptureFixture[str]) -> None:
    """When both fail, exit code is 1 and both errors are reported."""
    mock_imap = mock.MagicMock(spec=imaplib.IMAP4_SSL)
    mock_imap.login.side_effect = imaplib.IMAP4.error("BAD")
    mock_imap.sock = mock.MagicMock()

    mock_smtp = mock.MagicMock(spec=smtplib.SMTP)
    mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")

    with (
        mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 1
    err = capsys.readouterr().err
    # Both errors reported
    assert err.count("Error:") == 2


# ---------------------------------------------------------------------------
# probe - never calls send_message
# ---------------------------------------------------------------------------


def test_probe_never_calls_send_message(
    cfg: MailConfig,
) -> None:
    """The SMTP mock's send_message is never called."""
    mock_imap = _make_mock_imap_ssl()
    mock_smtp = _make_mock_smtp()

    with (
        mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        main(["probe"])

    mock_smtp.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# probe - connection refusal for IMAP
# ---------------------------------------------------------------------------


def test_probe_imap_connection_refused(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """probe handles IMAP connection-refused gracefully."""
    mock_smtp = _make_mock_smtp()

    with (
        mock.patch(
            "imaplib.IMAP4_SSL",
            side_effect=ConnectionRefusedError("Connection refused"),
        ),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Error:" in err
    assert "Connection refused" in err


# ---------------------------------------------------------------------------
# probe - connection refusal for SMTP
# ---------------------------------------------------------------------------


def test_probe_smtp_connection_refused(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """probe handles SMTP connection-refused gracefully."""
    mock_imap = _make_mock_imap_ssl()

    with (
        mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        mock.patch(
            "smtplib.SMTP",
            side_effect=ConnectionRefusedError("Connection refused"),
        ),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Error:" in err
    assert "Connection refused" in err


# ---------------------------------------------------------------------------
# probe - TLS failure for IMAP
# ---------------------------------------------------------------------------


def test_probe_imap_tls_failure(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """probe handles IMAP TLS failure gracefully (for STARTTLS)."""
    # Use a config with starttls so we can inject a TLS error
    cfg = MailConfig(
        imap_host="imap.example.com",
        imap_port=143,
        imap_tls_mode="starttls",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_tls_mode="starttls",
        username="user@example.com",
        password="s3cret",
    )

    mock_imap = mock.MagicMock(spec=imaplib.IMAP4)
    mock_imap.starttls.side_effect = ssl.SSLError("handshake failed")
    mock_imap.sock = mock.MagicMock()

    mock_smtp = _make_mock_smtp()

    with (
        mock.patch("imaplib.IMAP4", return_value=mock_imap),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Error:" in err
    assert "handshake" in err.lower()


# ---------------------------------------------------------------------------
# probe - SMTP STARTTLS failure
# ---------------------------------------------------------------------------


def test_probe_smtp_tls_failure(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """probe handles SMTP TLS failure gracefully."""
    mock_imap = _make_mock_imap_ssl()
    mock_smtp = mock.MagicMock(spec=smtplib.SMTP)
    mock_smtp.ehlo_or_helo_if_needed.return_value = (250, b"OK")
    mock_smtp.starttls.side_effect = ssl.SSLError("certificate verify failed")

    with (
        mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Error:" in err
    assert "STARTTLS" in err or "certificate" in err


# ---------------------------------------------------------------------------
# probe - IMAP authentication failure
# ---------------------------------------------------------------------------


def test_probe_imap_auth_failure(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """probe handles IMAP authentication failure gracefully."""
    mock_imap = mock.MagicMock(spec=imaplib.IMAP4_SSL)
    mock_imap.login.side_effect = imaplib.IMAP4.error(
        "AUTHENTICATIONFAILED invalid credentials"
    )
    mock_imap.sock = mock.MagicMock()

    mock_smtp = _make_mock_smtp()

    with (
        mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Error:" in err
    assert "Authentication failed" in err


# ---------------------------------------------------------------------------
# Config loading failure
# ---------------------------------------------------------------------------


def test_probe_config_load_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """probe exits with code 1 when config loading fails."""
    with mock.patch(
        "robotsix_auto_mail.config.MailConfig.from_env",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(SystemExit) as exc:
            main(["probe"])

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Error loading configuration" in err
    assert "boom" in err


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
# board subcommand
# ---------------------------------------------------------------------------


def test_parser_has_board_subcommand() -> None:
    """The parser knows the board subcommand."""
    parser = build_parser()
    args = parser.parse_args(["board"])
    assert args.command == "board"


def test_board_takes_no_extra_args() -> None:
    """board rejects extra arguments."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["board", "--foo"])


def test_board_empty_inbox(cfg: MailConfig, capsys: pytest.CaptureFixture[str]) -> None:
    """board prints a friendly message when the database is empty."""
    from robotsix_auto_mail.db import init_db as real_init_db

    conn = real_init_db(":memory:")  # schema lives in db.py — no DDL duplication
    # Keep conn open — _cmd_board's finally block closes it.

    with (
        mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg)),
        mock.patch("robotsix_auto_mail.cli.init_db", return_value=conn),
    ):
        rc = main(["board"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "Inbox" in captured.out
    assert "Your inbox is empty." in captured.out
    # No card-like content should appear
    assert "From:" not in captured.out
    # The header emits one 60-dash line; there should be no second one
    # (no card separator).
    assert captured.out.count("-" * 60) == 1


def test_board_with_records(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """board prints cards with sender, subject, date, body preview and count."""
    from robotsix_auto_mail.db import init_db as real_init_db

    conn = real_init_db(":memory:")  # schema lives in db.py — no DDL duplication
    conn.execute(
        """\
INSERT INTO mail_records
    (imap_uid, message_id, sender, subject, date,
     recipients_json, body_plain, body_html, attachments_json)
VALUES
    (?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
        (
            1,
            "<a@x.com>",
            "alice@example.com",
            "Hello",
            "2025-06-01T14:30:00",
            '{"to":[],"cc":[]}',
            "Just checking in!",
            "",
            "[]",
        ),
    )
    conn.execute(
        """\
INSERT INTO mail_records
    (imap_uid, message_id, sender, subject, date,
     recipients_json, body_plain, body_html, attachments_json)
VALUES
    (?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
        (
            2,
            "<b@x.com>",
            "bob@example.com",
            "Hi",
            "2025-06-02T09:15:00",
            '{"to":[],"cc":[]}',
            "See you at 10.\n\n--Bob",
            "",
            "[]",
        ),
    )
    conn.commit()
    # Keep conn open — _cmd_board's finally block closes it.

    with (
        mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg)),
        mock.patch("robotsix_auto_mail.cli.init_db", return_value=conn),
    ):
        rc = main(["board"])

    assert rc == 0
    captured = capsys.readouterr()
    out = captured.out

    assert "Inbox" in out
    assert "2 message(s)" in out

    # Card 1 content
    assert "alice@example.com" in out
    assert "Subject: Hello" in out
    assert "Date:    2025-06-01 14:30" in out
    assert "Just checking in!" in out

    # Card 2 content
    assert "bob@example.com" in out
    assert "Subject: Hi" in out
    assert "Date:    2025-06-02 09:15" in out
    assert "See you at 10." in out

    # Separator between cards (dashed line) — plus one from the header = 2
    assert out.count("-" * 60) == 2

    # No empty-inbox message
    assert "Your inbox is empty." not in out


def test_board_body_preview_truncation(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """Body preview truncates at 150 chars with '…' only when longer."""
    from robotsix_auto_mail.db import init_db as real_init_db

    # Body exactly at the limit — no ellipsis
    body_150 = "x" * 150
    # Body over the limit — should truncate with ellipsis
    body_200 = "y" * 200

    conn = real_init_db(":memory:")
    conn.execute(
        """\
INSERT INTO mail_records
    (imap_uid, message_id, sender, subject, date,
     recipients_json, body_plain, body_html, attachments_json)
VALUES
    (?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
        (
            1,
            "<a@x.com>",
            "a@x.com",
            "150 chars",
            "2025-06-01T14:30:00",
            '{"to":[],"cc":[]}',
            body_150,
            "",
            "[]",
        ),
    )
    conn.execute(
        """\
INSERT INTO mail_records
    (imap_uid, message_id, sender, subject, date,
     recipients_json, body_plain, body_html, attachments_json)
VALUES
    (?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
        (
            2,
            "<b@x.com>",
            "b@x.com",
            "200 chars",
            "2025-06-02T09:15:00",
            '{"to":[],"cc":[]}',
            body_200,
            "",
            "[]",
        ),
    )
    conn.commit()

    with (
        mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg)),
        mock.patch("robotsix_auto_mail.cli.init_db", return_value=conn),
    ):
        rc = main(["board"])

    assert rc == 0
    out = capsys.readouterr().out

    # 150-char body: full text, no ellipsis in its card
    assert body_150 in out
    assert body_150 + "\u2026" not in out

    # 200-char body: truncated at 150 chars + ellipsis
    truncated = body_200[:150] + "\u2026"
    assert truncated in out
    assert body_200 not in out  # full 200-char string not present


def test_board_config_load_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """board exits with code 1 when config loading fails."""
    with mock.patch(
        "robotsix_auto_mail.cli.load_accounts",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(SystemExit) as exc:
            main(["board"])

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Error loading configuration" in err
    assert "boom" in err


def test_board_header_uses_print_header(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """board output includes the _print_header-style header."""
    from robotsix_auto_mail.db import init_db as real_init_db

    conn = real_init_db(":memory:")  # schema lives in db.py — no DDL duplication
    # Keep conn open — _cmd_board's finally block closes it.

    with (
        mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg)),
        mock.patch("robotsix_auto_mail.cli.init_db", return_value=conn),
    ):
        main(["board"])

    captured = capsys.readouterr()
    # _print_header produces:
    # "\nInbox\n------------------------------------------------------------\n"
    assert "\nInbox\n" in captured.out
    assert "-" * 60 in captured.out


def test_board_does_not_mutate_database(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """main(["board"]) must not add, delete, or modify any rows in the database."""
    import os
    import sqlite3
    import tempfile

    from robotsix_auto_mail.db import init_db as real_init_db

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    try:
        conn = real_init_db(db_path)

        # Pre-populate with 2 records whose values we can snapshot.
        row1 = (
            10,
            "<x@a.com>",
            "alice@x.com",
            "Hello",
            "2025-01-01T12:00:00",
            '{"to":["bob@x.com"],"cc":[]}',
            "Body A",
            "<p>Body A</p>",
            '[{"name":"a.txt"}]',
        )
        row2 = (
            20,
            "<y@b.com>",
            "bob@x.com",
            "Hi",
            "2025-01-02T13:00:00",
            '{"to":["carol@x.com"],"cc":[]}',
            "Body B",
            "<p>Body B</p>",
            "[]",
        )
        conn.execute(
            """\
INSERT INTO mail_records
    (imap_uid, message_id, sender, subject, date,
     recipients_json, body_plain, body_html, attachments_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
            row1,
        )
        conn.execute(
            """\
INSERT INTO mail_records
    (imap_uid, message_id, sender, subject, date,
     recipients_json, body_plain, body_html, attachments_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
            row2,
        )
        conn.commit()

        # Snapshot the full table state before the board command runs.
        def _snapshot(c: sqlite3.Connection) -> dict[str, Any]:

            cur = c.execute("SELECT * FROM mail_records ORDER BY id")
            col_names = [desc[0] for desc in cur.description]
            rows = [dict(zip(col_names, r, strict=True)) for r in cur.fetchall()]
            cur = c.execute("SELECT COUNT(*) FROM watermark")
            wm_count = cur.fetchone()[0]
            return {"mail_records": rows, "watermark_count": wm_count}

        before = _snapshot(conn)
        conn.close()

        # Now run main(["board"]) — it will call init_db(db_path) via load().
        # We patch only load(); init_db will be the real one, which opens the
        # same file-backed database.  The db_path comes from the config.
        cfg_with_db = MailConfig(
            imap_host="imap.example.com",
            imap_port=993,
            imap_tls_mode="direct-tls",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            username="user@example.com",
            password="s3cret",
            db_path=db_path,
        )

        with mock.patch(
            "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_with_db)
        ):
            rc = main(["board"])

        assert rc == 0

        # Re-open the same file and snapshot again.
        conn2 = real_init_db(db_path)
        after = _snapshot(conn2)
        conn2.close()

        # Row count must be identical.
        assert len(after["mail_records"]) == 2
        assert len(after["mail_records"]) == len(before["mail_records"])

        # Every column of every row must be bit-for-bit unchanged.
        for i, (b_row, a_row) in enumerate(
            zip(before["mail_records"], after["mail_records"], strict=True)
        ):
            for col in b_row:
                assert a_row[col] == b_row[col], (
                    f"Row {i} column {col} changed: {b_row[col]!r} -> {a_row[col]!r}"
                )

        # Watermark table must be untouched.
        assert after["watermark_count"] == before["watermark_count"]

        # No interactive prompts or write-action indicators in output.
        captured = capsys.readouterr()
        assert "write" not in captured.out.lower()
        assert "TO_DELETE" not in captured.out.lower()
        assert "edit" not in captured.out.lower()
        assert "modify" not in captured.out.lower()
        assert "select an action" not in captured.out.lower()

    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# detect subcommand
# ---------------------------------------------------------------------------


def test_parser_has_detect_subcommand() -> None:
    """The parser knows the detect subcommand with expected defaults."""
    parser = build_parser()
    args = parser.parse_args(["detect", "user@gmail.com"])
    assert args.command == "detect"
    assert args.email == "user@gmail.com"
    assert args.stdout is False
    assert args.output == "config/mail.local.yaml"


def test_detect_missing_pydantic_ai(capsys: pytest.CaptureFixture[str]) -> None:
    """detect exits 1 when pydantic_ai package is not installed."""
    import sys

    # Remove detect module from cache so the lazy import inside
    # _cmd_detect is forced to re-import (and we can block it).
    real_detect = sys.modules.pop("robotsix_auto_mail.detect", None)
    original_import = builtins.__import__

    def _block_detect(
        name: str,
        *args: object,
        **kwargs: object,
    ) -> object:
        if name == "robotsix_auto_mail.detect":
            raise ImportError("No module named 'pydantic_ai'")
        return original_import(name, *args, **kwargs)  # type: ignore[arg-type]

    try:
        with mock.patch("builtins.__import__", side_effect=_block_detect):
            rc = main(["detect", "user@gmail.com"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "requires the pydantic-ai package" in err
    finally:
        if real_detect is not None:
            sys.modules["robotsix_auto_mail.detect"] = real_detect


@pytest.fixture
def no_autoconfig() -> object:
    """Force autoconfig + MX detection to miss so tests reach the LLM path."""
    with (
        mock.patch("robotsix_auto_mail.detect.autoconfig_lookup", return_value=None),
        mock.patch("robotsix_auto_mail.detect.mx_lookup", return_value=[]),
        mock.patch("robotsix_auto_mail.detect.provider_from_mx", return_value=None),
    ):
        yield


def _ok_result() -> object:
    from robotsix_auto_mail.cli import _VerifyResult

    return _VerifyResult(imap_ok=True, smtp_ok=True)


def _auth_fail_result() -> object:
    from robotsix_auto_mail.cli import _VerifyResult

    return _VerifyResult(
        imap_ok=False,
        smtp_ok=False,
        imap_auth=True,
        smtp_auth=True,
        imap_error="auth",
        smtp_error="auth",
    )


def _host_fail_result() -> object:
    """IMAP host unreachable, SMTP ok — a connection (not auth) failure."""
    from robotsix_auto_mail.cli import _VerifyResult

    return _VerifyResult(
        imap_ok=False,
        smtp_ok=True,
        imap_error="connection refused",
    )


def test_detect_happy_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect writes a single config file (password included) on success."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
    )

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch("getpass.getpass", return_value="testpass"),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["detect", "user@gmail.com", "--output", str(output), "--no-verify"])

    assert rc == 0
    content = output.read_text()
    assert "imap.gmail.com" in content
    assert "smtp.gmail.com" in content
    assert "user@gmail.com" in content
    # Password is written into the config file itself — no separate file.
    assert "testpass" in content
    assert not (tmp_path / "secrets.yaml").exists()

    captured = capsys.readouterr()
    assert "Config written" in captured.err


def test_detect_password_supplied(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect --password skips the interactive prompt and writes the config."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
    )

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch("getpass.getpass") as mock_getpass,
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--output",
                str(output),
                "--password",
                "cli-pass",
                "--no-verify",
            ]
        )

    assert rc == 0
    mock_getpass.assert_not_called()

    content = output.read_text()
    assert "cli-pass" in content
    assert not (tmp_path / "secrets.yaml").exists()


def test_detect_empty_password(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect with an empty password writes the config and warns the user."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
    )

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch("getpass.getpass", return_value=""),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["detect", "user@gmail.com", "--output", str(output)])

    assert rc == 0
    content = output.read_text()
    assert "imap.gmail.com" in content

    captured = capsys.readouterr()
    assert "No password provided" in captured.err


def test_detect_stdout(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect --stdout prints config and emits a verification banner."""
    mock_provider = MailProvider(
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
    )

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["detect", "user@gmail.com", "--stdout"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "imap.gmail.com" in captured.out
    assert "smtp.gmail.com" in captured.out
    assert "user@gmail.com" in captured.out
    assert "verify" in captured.err.lower()


def test_detect_stdout_redacts_password(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect --stdout --password omits the password from the printed config.

    The supplied password must NOT leak to stdout; instead the rendered
    config carries the empty-password placeholder hinting at MAIL_PASSWORD.
    """
    mock_provider = MailProvider(
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
    )

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["detect", "user@gmail.com", "--stdout", "--password", "cli-pass"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "imap.gmail.com" in captured.out
    assert "cli-pass" not in captured.out
    # The rendered (stdout) config redacts the password; the MAIL_PASSWORD
    # hint is printed to stderr alongside the multi-account YAML.
    assert 'password: ""' in captured.out
    assert "MAIL_PASSWORD" in captured.err


def test_detect_detection_error(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect exits 1 when DetectionError is raised (and autoconfig missed)."""
    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider",
            side_effect=DetectionError("test error"),
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["detect", "user@gmail.com", "--stdout"])

    assert rc == 1
    captured = capsys.readouterr()
    assert "test error" in captured.err


def test_detect_llm_api_key_env(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect passes LLM_API_KEY from the environment to
    detect_provider (model is no longer forwarded — the tier bakes the model
    choice)."""
    mock_provider = MailProvider(
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
    )
    mock_dp = mock.MagicMock(return_value=mock_provider)

    with mock.patch.dict(
        os.environ,
        {"LLM_API_KEY": "sk-test"},
    ):
        with mock.patch("robotsix_auto_mail.detect.detect_provider", mock_dp):
            rc = main(["detect", "user@x.com", "--stdout"])

    assert rc == 0
    mock_dp.assert_called_once_with("user@x.com", api_key="sk-test", mx_hosts=[])


def test_detect_uses_autoconfig_when_available(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When autoconfig resolves, the LLM is not consulted."""
    output = tmp_path / "cfg.yaml"
    autoconf_provider = MailProvider(
        imap_host="imap.fromautoconfig.net",
        smtp_host="smtp.fromautoconfig.net",
    )
    mock_llm = mock.MagicMock()

    with (
        mock.patch(
            "robotsix_auto_mail.detect.autoconfig_lookup",
            return_value=autoconf_provider,
        ),
        mock.patch("robotsix_auto_mail.detect.detect_provider", mock_llm),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@custom.net",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
            ]
        )

    assert rc == 0
    mock_llm.assert_not_called()
    assert "imap.fromautoconfig.net" in output.read_text()
    assert "autoconfig" in capsys.readouterr().err


def test_detect_verifies_connection_on_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """After writing the config, detect verifies by connecting (default)."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch(
            "robotsix_auto_mail.cli._verify_config", return_value=_ok_result()
        ) as mock_verify,
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
            ]
        )

    assert rc == 0
    mock_verify.assert_called_once()
    assert mock_verify.call_args.args[0].password == "pw"
    assert "Verification succeeded" in capsys.readouterr().err


def test_detect_verify_failure_returns_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """A failed verification (auth, no retries) surfaces as exit code 1."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    # --password ⇒ no interactive password retry budget, so an auth-only
    # failure ends the loop immediately.
    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch(
            "robotsix_auto_mail.cli._verify_config",
            return_value=_auth_fail_result(),
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
            ]
        )

    assert rc == 1
    assert output.exists()
    assert "Verification FAILED" in capsys.readouterr().err


def test_detect_no_verify_skips_check(tmp_path: Path, no_autoconfig: object) -> None:
    """--no-verify writes the config without connecting."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch("robotsix_auto_mail.cli._verify_config") as mock_verify,
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
            ]
        )

    assert rc == 0
    mock_verify.assert_not_called()


def test_detect_microsoft_runs_device_code_and_verifies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """A Microsoft address writes an OAuth2 block, runs device-code login, and
    verifies over XOAUTH2 — never prompting for or writing a password."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch("getpass.getpass") as mock_getpass,
        mock.patch("robotsix_auto_mail.oauth2.device_code_login") as mock_login,
        mock.patch(
            "robotsix_auto_mail.cli._verify_config", return_value=_ok_result()
        ) as mock_verify,
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["detect", "user@contoso.com", "--output", str(output)])

    assert rc == 0
    mock_getpass.assert_not_called()
    mock_login.assert_called_once()
    mock_verify.assert_called_once()
    content = output.read_text()
    assert 'oauth2_provider: "microsoft"' in content
    assert "password:" not in content
    assert "Verification succeeded" in capsys.readouterr().err


def test_detect_microsoft_stdout_instructs_auth_login(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--stdout for a Microsoft address emits the OAuth2 YAML and tells the
    user to run `auth login`, without any interactive flow."""
    mock_provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch("getpass.getpass") as mock_getpass,
        mock.patch("robotsix_auto_mail.oauth2.device_code_login") as mock_login,
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["detect", "user@contoso.com", "--stdout"])

    assert rc == 0
    mock_getpass.assert_not_called()
    mock_login.assert_not_called()
    captured = capsys.readouterr()
    assert 'oauth2_provider: "microsoft"' in captured.out
    assert "password:" not in captured.out
    assert "auth login" in captured.err


def test_detect_microsoft_auth_failure_points_at_auth_login(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """A Microsoft auth failure surfaces an actionable message and never
    re-prompts for a password."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch("getpass.getpass") as mock_getpass,
        mock.patch("robotsix_auto_mail.oauth2.device_code_login"),
        mock.patch(
            "robotsix_auto_mail.cli._verify_config",
            return_value=_auth_fail_result(),
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["detect", "user@contoso.com", "--output", str(output)])

    assert rc == 1
    mock_getpass.assert_not_called()
    err = capsys.readouterr().err
    assert "auth login" in err


def test_detect_refines_host_with_llm_on_connection_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """A connection failure triggers an LLM refinement that then succeeds."""
    output = tmp_path / "cfg.yaml"
    bad = MailProvider(imap_host="imap.bad.net", smtp_host="smtp.gmail.com")
    good = MailProvider(imap_host="imap.good.net", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider",
            side_effect=[bad, good],
        ) as mock_dp,
        mock.patch(
            "robotsix_auto_mail.cli._verify_config",
            side_effect=[_host_fail_result(), _ok_result()],
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
            ]
        )

    assert rc == 0
    # initial guess + one refinement
    assert mock_dp.call_count == 2
    # the refinement was given failure feedback
    assert mock_dp.call_args.kwargs.get("feedback")
    assert "imap.good.net" in output.read_text()
    assert "Refining" in capsys.readouterr().err


def test_detect_prompts_for_host_when_llm_cannot_fix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """When LLM refinement errors, detect prompts for the host, then verifies."""
    output = tmp_path / "cfg.yaml"
    bad = MailProvider(imap_host="imap.bad.net", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider",
            side_effect=[bad, DetectionError("llm down")],
        ),
        mock.patch(
            "robotsix_auto_mail.cli._verify_config",
            side_effect=[_host_fail_result(), _ok_result()],
        ),
        mock.patch("builtins.input", return_value="mail.manual.net") as mock_input,
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
            ]
        )

    assert rc == 0
    mock_input.assert_called()
    assert "mail.manual.net" in output.read_text()
    assert "manually" in capsys.readouterr().err


def test_detect_preserves_existing_llm_section(
    tmp_path: Path, no_autoconfig: object
) -> None:
    """Re-running detect over a file keeps its llm: section."""
    output = tmp_path / "mail.local.yaml"
    output.write_text(
        """\
imap:
  host: old.example.com

smtp:
  host: old.example.com

auth:
  username: old@example.com

llm:
  api_key: sk-keep-me
"""
    )
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
            ]
        )

    assert rc == 0
    content = output.read_text()
    # mail fields updated…
    assert "imap.gmail.com" in content
    assert "user@gmail.com" in content
    # …but the llm api key is preserved
    assert "sk-keep-me" in content


def test_detect_honours_id_flag(tmp_path: Path, no_autoconfig: object) -> None:
    """detect --id sets the account id and the .data/<id>/mail.db store path."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
                "--id",
                "personal",
            ]
        )

    assert rc == 0
    accounts = MailAccountsConfig.from_yaml(str(output))
    assert accounts.ids() == ("personal",)
    assert accounts.default_account_id == "personal"
    assert accounts.get("personal").config.db_path == ".data/personal/mail.db"


def test_detect_appends_second_account(tmp_path: Path, no_autoconfig: object) -> None:
    """A second detect against an existing multi-account file appends, not clobbers."""
    output = tmp_path / "cfg.yaml"
    p1 = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")
    p2 = MailProvider(imap_host="imap.work.com", smtp_host="smtp.work.com")

    with (
        mock.patch("robotsix_auto_mail.detect.detect_provider", side_effect=[p1, p2]),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc1 = main(
            [
                "detect",
                "me@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
                "--id",
                "personal",
            ]
        )
        rc2 = main(
            [
                "detect",
                "me@work.com",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
                "--id",
                "work",
            ]
        )

    assert rc1 == 0
    assert rc2 == 0
    accounts = MailAccountsConfig.from_yaml(str(output))
    assert set(accounts.ids()) == {"personal", "work"}
    assert accounts.get("work").config.imap_host == "imap.work.com"


def test_detect_refuses_duplicate_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """A detect whose resolved id already exists is refused (exit 1) without clobber."""
    output = tmp_path / "cfg.yaml"
    provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch("robotsix_auto_mail.detect.detect_provider", return_value=provider),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc1 = main(
            [
                "detect",
                "me@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
                "--id",
                "personal",
            ]
        )
        capsys.readouterr()
        rc2 = main(
            [
                "detect",
                "other@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
                "--id",
                "personal",
            ]
        )

    assert rc1 == 0
    assert rc2 == 1
    assert "already exists" in capsys.readouterr().err
    accounts = MailAccountsConfig.from_yaml(str(output))
    assert accounts.ids() == ("personal",)


# ---------------------------------------------------------------------------
# migrate-config
# ---------------------------------------------------------------------------


_MONO_CONFIG = (
    "imap:\n  host: imap.example.com\n  port: 1993\n"
    "smtp:\n  host: smtp.example.com\n"
    'auth:\n  username: u@example.com\n  password: "s3cret"\n'
    "llm:\n  api_key: sk-keep\n"
)


def test_migrate_config_converts_mono_and_writes_backup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """migrate-config rewrites a mono file to accounts shape, preserving values."""
    cfg = tmp_path / "mail.local.yaml"
    cfg.write_text(_MONO_CONFIG)

    rc = main(["migrate-config", "--config", str(cfg)])

    assert rc == 0
    backup = tmp_path / "mail.local.yaml.bak"
    assert backup.exists()
    assert backup.read_text() == _MONO_CONFIG
    migrated = MailAccountsConfig.from_yaml(str(cfg))
    assert migrated.ids() == ("default",)
    acct = migrated.default.config
    assert acct.imap_host == "imap.example.com"
    assert acct.imap_port == 1993
    assert acct.password == "s3cret"
    assert acct.llm_api_key == "sk-keep"
    assert acct.db_path == ".data/default/mail.db"


def test_migrate_config_custom_id(tmp_path: Path) -> None:
    """migrate-config --id sets the migrated account id and store folder."""
    cfg = tmp_path / "mail.local.yaml"
    cfg.write_text(_MONO_CONFIG)

    rc = main(["migrate-config", "--config", str(cfg), "--id", "personal"])

    assert rc == 0
    migrated = MailAccountsConfig.from_yaml(str(cfg))
    assert migrated.ids() == ("personal",)
    assert migrated.default.config.db_path == ".data/personal/mail.db"


def test_migrate_config_idempotent_on_multi(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """migrate-config is a no-op (exit 0) on an already-multi file."""
    cfg = tmp_path / "mail.local.yaml"
    multi = (
        "default_account: a\naccounts:\n  - id: a\n"
        "    imap:\n      host: i\n    smtp:\n      host: s\n"
        '    auth:\n      username: u\n      password: "p"\n'
        "    store:\n      path: .data/a/mail.db\n"
    )
    cfg.write_text(multi)

    rc = main(["migrate-config", "--config", str(cfg)])

    assert rc == 0
    assert "already" in capsys.readouterr().out.lower()
    assert cfg.read_text() == multi
    assert not (tmp_path / "mail.local.yaml.bak").exists()


def test_migrate_config_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """migrate-config errors (exit 1) on a missing file."""
    rc = main(["migrate-config", "--config", str(tmp_path / "nope.yaml")])

    assert rc == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_migrate_config_dry_run_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--dry-run prints the migrated YAML without writing the file or backup."""
    cfg = tmp_path / "mail.local.yaml"
    cfg.write_text(_MONO_CONFIG)

    rc = main(["migrate-config", "--config", str(cfg), "--dry-run"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "accounts:" in out
    assert "default_account:" in out
    assert cfg.read_text() == _MONO_CONFIG
    assert not (tmp_path / "mail.local.yaml.bak").exists()


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
        "robotsix_auto_mail.config.config_sync_agent.get_provider",
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
    cls.assert_called_once_with(api_key="sk-cli")


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


# ---------------------------------------------------------------------------
# triage-folder subcommand
# ---------------------------------------------------------------------------


def _raw_folder_message(message_id: str = "<a@x.com>") -> bytes:
    """Build a minimal MIME message for the triage-folder CLI tests."""
    return (
        f"From: alice@example.com\r\n"
        f"To: bob@example.com\r\n"
        f"Subject: Hello\r\n"
        f"Date: Wed, 15 Jan 2025 10:30:00 +0000\r\n"
        f"Message-ID: {message_id}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n"
        f"body"
    ).encode("utf-8")


def _cfg_empty_db(tmp_path: Path, name: str = "folder.db") -> MailConfig:
    """A MailConfig pointing at an empty (uncreated) temp DB."""
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        db_path=str(tmp_path / name),
    )


def _patch_folder_imap(*messages: bytes) -> mock._patch[mock.MagicMock]:
    """Patch the commands-module ImapClient to yield *messages* for a folder."""
    mock_imap = mock.MagicMock(spec=ImapClient)
    uids = list(range(1, len(messages) + 1))
    mock_imap.search_uids.return_value = uids
    mock_imap.fetch_messages.return_value = list(zip(uids, messages, strict=True))

    imap_cls = mock.MagicMock()
    imap_cls.return_value.__enter__.return_value = mock_imap
    return mock.patch("robotsix_auto_mail.cli.commands.ImapClient", imap_cls)


def test_parser_has_triage_folder_subcommand() -> None:
    """The parser knows triage-folder with positional folder and flag defaults."""
    args = build_parser().parse_args(["triage-folder", "Archive"])
    assert args.command == "triage-folder"
    assert args.folder == "Archive"
    assert args.output_format == "text"
    assert args.api_key is None
    assert args.dry_run is False


def test_triage_folder_text_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """triage-folder ingests the folder then triages, rendering both (text)."""
    cfg_db = _cfg_empty_db(tmp_path)
    result = TriageResult(
        items=[TriageItem(index=1, action="TO_ARCHIVE", reason="reference mail")]
    )
    with (
        _patch_folder_imap(_raw_folder_message("<a@x.com>")),
        _patch_triage_llm(result),
        mock.patch(
            "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["triage-folder", "Archive"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "Fetched:  1 messages" in out
    assert "Stored:   1 new" in out
    assert "Folder Triage" in out
    assert "<a@x.com>" in out
    assert "TO_ARCHIVE" in out
    assert "reference mail" in out


def test_triage_folder_dry_run_skips_triage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """triage-folder --dry-run stores nothing and skips the triage pass."""
    cfg_db = _cfg_empty_db(tmp_path)
    with (
        _patch_folder_imap(_raw_folder_message("<a@x.com>")),
        mock.patch("robotsix_llmio.core.get_provider") as cls,
        mock.patch(
            "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
        ),
    ):
        rc = main(["triage-folder", "Archive", "--dry-run"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY RUN — nothing stored" in out
    assert "No mail to triage." in out
    # No triage pass occurred.
    cls.assert_not_called()


def test_triage_folder_json_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """triage-folder --output-format json emits ingest counts and decisions."""
    cfg_db = _cfg_empty_db(tmp_path)
    result = TriageResult(
        items=[TriageItem(index=1, action="TO_ARCHIVE", confidence="high")]
    )
    with (
        _patch_folder_imap(_raw_folder_message("<a@x.com>")),
        _patch_triage_llm(result),
        mock.patch(
            "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["triage-folder", "Archive", "--output-format", "json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["folder"] == "Archive"
    assert payload["fetched"] == 1
    assert payload["stored"] == 1
    assert payload["skipped"] == 0
    assert payload["errors"] == 0
    assert payload["decisions"][0]["message_id"] == "<a@x.com>"
    assert payload["decisions"][0]["action"] == "TO_ARCHIVE"


def test_triage_folder_imap_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An ImapError returns 1 with an Error: line on stderr."""
    from robotsix_auto_mail.imap import ImapError

    cfg_db = _cfg_empty_db(tmp_path)
    imap_cls = mock.MagicMock(side_effect=ImapError("connection refused"))
    with (
        mock.patch("robotsix_auto_mail.cli.commands.ImapClient", imap_cls),
        mock.patch(
            "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
        ),
    ):
        rc = main(["triage-folder", "Archive"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Error:" in err
    assert "connection refused" in err


def test_triage_folder_triage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A TriageError returns 1 with an Error: line on stderr."""
    cfg_db = _cfg_empty_db(tmp_path)
    with (
        _patch_folder_imap(_raw_folder_message("<a@x.com>")),
        mock.patch(
            "robotsix_auto_mail.triage.run_triage_agent",
            side_effect=TriageError("llm exploded"),
        ),
        mock.patch(
            "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
        ),
    ):
        rc = main(["triage-folder", "Archive"])

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
# triage-rules / triage-rules-set subcommands
# ---------------------------------------------------------------------------


def _cfg_with_rule_history(
    tmp_path: Path, sender: str = "alice@example.com", count: int = 3
) -> MailConfig:
    """A MailConfig whose DB has *count* consistent 'TO_ARCHIVE' decisions."""
    from robotsix_auto_mail.db import MailRecord, insert_record
    from robotsix_auto_mail.db import init_db as real_init_db
    from robotsix_auto_mail.triage import set_triage_decision

    db_path = str(tmp_path / "rules.db")
    conn = real_init_db(db_path)
    for i in range(count):
        mid = f"<m{i}@x.com>"
        insert_record(
            conn,
            MailRecord(
                message_id=mid,
                sender=sender,
                subject="Hello",
                date="2025-06-01T12:00:00",
                body_plain="hi",
            ),
        )
        set_triage_decision(conn, mid, "TO_ARCHIVE", source="agent")
    conn.close()
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        db_path=db_path,
    )


def test_parser_has_triage_rules_subcommand() -> None:
    """The parser knows triage-rules with its output-format default."""
    args = build_parser().parse_args(["triage-rules", "--output-format", "json"])
    assert args.command == "triage-rules"
    assert args.output_format == "json"


def test_parser_has_triage_rules_set_subcommand() -> None:
    """The parser knows triage-rules-set with positional args."""
    args = build_parser().parse_args(["triage-rules-set", "abc123", "accepted"])
    assert args.command == "triage-rules-set"
    assert args.fingerprint == "abc123"
    assert args.state == "accepted"


def test_triage_rules_text_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """triage-rules proposes a rule and lists active rules (text, exit 0)."""
    cfg_db = _cfg_with_rule_history(tmp_path)
    with mock.patch(
        "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
    ):
        rc = main(["triage-rules"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "Triage Rule Proposals" in out
    assert "alice@example.com" in out
    assert "TO_ARCHIVE" in out
    assert "fingerprint:" in out
    assert "Active rules:" in out


def test_triage_rules_json_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """triage-rules --output-format json prints proposals + active rules."""
    cfg_db = _cfg_with_rule_history(tmp_path)
    with mock.patch(
        "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
    ):
        rc = main(["triage-rules", "--output-format", "json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["proposals"]) == 1
    proposal = payload["proposals"][0]
    assert proposal["match_type"] == "sender"
    assert proposal["match_value"] == "alice@example.com"
    assert proposal["action"] == "TO_ARCHIVE"
    assert proposal["fingerprint"]
    assert payload["active_rules"] == []


def test_triage_rules_dedup_on_second_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A second triage-rules run suppresses the already-recorded proposal."""
    cfg_db = _cfg_with_rule_history(tmp_path)
    with mock.patch(
        "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
    ):
        main(["triage-rules", "--output-format", "json"])
        capsys.readouterr()
        rc = main(["triage-rules", "--output-format", "json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["proposals"] == []


def test_triage_rules_set_accept_makes_active(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """triage-rules-set accepted adds the rule to the active set (exit 0)."""
    from robotsix_auto_mail.db import init_db as real_init_db
    from robotsix_auto_mail.triage import _load_active_rules

    cfg_db = _cfg_with_rule_history(tmp_path)
    with mock.patch(
        "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
    ):
        main(["triage-rules", "--output-format", "json"])
        fingerprint = json.loads(capsys.readouterr().out)["proposals"][0]["fingerprint"]
        rc = main(["triage-rules-set", fingerprint, "accepted"])

    assert rc == 0
    assert "Recorded triage rule state" in capsys.readouterr().out
    conn = real_init_db(cfg_db.db_path)
    try:
        active = _load_active_rules(conn)
        assert len(active) == 1
        assert active[0].match_value == "alice@example.com"
        assert active[0].action == "TO_ARCHIVE"
    finally:
        conn.close()


def test_triage_rules_set_unknown_fingerprint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """triage-rules-set exits 1 with a clear message on unknown fingerprint."""
    cfg_db = _cfg_with_rule_history(tmp_path)
    with mock.patch(
        "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
    ):
        rc = main(["triage-rules-set", "deadbeef", "accepted"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "No triage rule proposal" in err
    assert "deadbeef" in err


def test_triage_rules_set_invalid_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """triage-rules-set exits 1 with a clear message on an invalid state."""
    cfg_db = _cfg_with_rule_history(tmp_path)
    with mock.patch(
        "robotsix_auto_mail.cli.load_accounts", return_value=_accounts(cfg_db)
    ):
        rc = main(["triage-rules-set", "deadbeef", "pending"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "invalid state" in err
    assert "pending" in err


def _refine_test_config() -> MailConfig:
    """Build a minimal MailConfig for refinement-helper unit tests."""
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
    )


def _refine_host_result() -> "_VerifyResult":
    """Build an IMAP-host-failure _VerifyResult for helper unit tests."""
    from robotsix_auto_mail.cli import _VerifyResult

    return _VerifyResult(imap_ok=False, smtp_ok=True, imap_error="connection refused")


def test_refine_password_returns_rebuilt_config() -> None:
    """_refine_password rebuilds the config from a freshly entered password."""
    from robotsix_auto_mail.cli import _refine_password

    provider = MailProvider(imap_host="imap.x.net", smtp_host="smtp.x.net")
    rebuilt = _refine_test_config()
    build = mock.MagicMock(return_value=rebuilt)

    with mock.patch("getpass.getpass", return_value="newpw"):
        outcome = _refine_password(build, provider)

    assert outcome.config is rebuilt
    assert outcome.provider is None
    build.assert_called_once_with(provider, "newpw")


def test_refine_password_stops_on_empty_input() -> None:
    """_refine_password signals stop (config None) on empty input."""
    from robotsix_auto_mail.cli import _refine_password

    provider = MailProvider(imap_host="imap.x.net", smtp_host="smtp.x.net")
    build = mock.MagicMock()

    with mock.patch("getpass.getpass", return_value=""):
        outcome = _refine_password(build, provider)

    assert outcome.config is None
    build.assert_not_called()


def test_refine_password_stops_on_cancel() -> None:
    """_refine_password signals stop when the prompt is cancelled."""
    from robotsix_auto_mail.cli import _refine_password

    provider = MailProvider(imap_host="imap.x.net", smtp_host="smtp.x.net")
    build = mock.MagicMock()

    with mock.patch("getpass.getpass", side_effect=KeyboardInterrupt):
        outcome = _refine_password(build, provider)

    assert outcome.config is None
    build.assert_not_called()


def test_refine_with_llm_success_returns_provider_and_config() -> None:
    """_refine_with_llm returns the refined provider and rebuilt config."""
    from robotsix_auto_mail.cli import _refine_with_llm

    provider = MailProvider(imap_host="imap.bad.net", smtp_host="smtp.x.net")
    refined = MailProvider(imap_host="imap.good.net", smtp_host="smtp.x.net")
    rebuilt = _refine_test_config()
    build = mock.MagicMock(return_value=rebuilt)
    config = _refine_test_config()
    result = _refine_host_result()

    outcome = _refine_with_llm(
        build,
        provider,
        config,
        result,
        email="user@example.com",
        api_key="sk-test",
        mx_hosts=[],
        detect_provider=mock.MagicMock(return_value=refined),
        _detection_error=DetectionError,
    )

    assert outcome.provider is refined
    assert outcome.config is rebuilt
    build.assert_called_once_with(refined, config.password)


def test_refine_with_llm_detection_error_returns_empty(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_refine_with_llm reports the error and returns no refinement."""
    from robotsix_auto_mail.cli import _refine_with_llm

    provider = MailProvider(imap_host="imap.bad.net", smtp_host="smtp.x.net")
    build = mock.MagicMock()

    outcome = _refine_with_llm(
        build,
        provider,
        _refine_test_config(),
        _refine_host_result(),
        email="user@example.com",
        api_key="sk-test",
        mx_hosts=[],
        detect_provider=mock.MagicMock(side_effect=DetectionError("down")),
        _detection_error=DetectionError,
    )

    assert outcome.config is None
    assert outcome.provider is None
    build.assert_not_called()
    assert "LLM refinement error: down" in capsys.readouterr().err


def test_refine_manual_returns_updated_config() -> None:
    """_refine_manual returns the config produced by _prompt_hosts."""
    from robotsix_auto_mail.cli import _refine_manual

    updated = _refine_test_config()
    with mock.patch("robotsix_auto_mail.cli._prompt_hosts", return_value=updated):
        outcome = _refine_manual(_refine_test_config(), _refine_host_result())

    assert outcome.config is updated


def test_refine_manual_stops_when_prompt_returns_none() -> None:
    """_refine_manual signals stop when _prompt_hosts returns None."""
    from robotsix_auto_mail.cli import _refine_manual

    with mock.patch("robotsix_auto_mail.cli._prompt_hosts", return_value=None):
        outcome = _refine_manual(_refine_test_config(), _refine_host_result())

    assert outcome.config is None


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
        "triage-rules",
        "triage-rules-set",
        "config-sync",
        "config-sync-set",
    ],
)
def test_account_flag_accepted_by_subcommands(command: str) -> None:
    """Every account-consuming subcommand accepts ``--account ID``."""
    extra = {
        "triage-set": ["m@id", "INBOX"],
        "triage-rules-set": ["fp", "accepted"],
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
    assert "personal" in err and "work" in err


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


# ---------------------------------------------------------------------------
# _clear_stale_triage_state — boot-clear of orphaned triage_run:state
# ---------------------------------------------------------------------------


def test_clear_stale_triage_state_resets_running_flags(
    cfg: MailConfig, tmp_path: Path
) -> None:
    """A boot-clear resets every orphaned ``running`` flag to ``idle`` while
    leaving non-running flags untouched and tolerating a missing account DB."""
    from robotsix_auto_mail.cli.commands import _clear_stale_triage_state
    from robotsix_auto_mail.db import get_watermark, init_db, set_watermark

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
