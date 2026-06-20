"""Tests for the CLI board subcommand."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from typing import Any
from unittest import mock

import pytest

from robotsix_auto_mail.cli import build_parser, main
from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig


def _accounts(cfg: MailConfig, account_id: str = "default") -> MailAccountsConfig:
    """Wrap a single ``MailConfig`` in a one-element accounts container."""
    return MailAccountsConfig(
        accounts=(MailAccount(account_id=account_id, config=cfg, label=None),),
        default_account_id=account_id,
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
        mock.patch("robotsix_auto_mail.cli.commands_board.init_db", return_value=conn),
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
        mock.patch("robotsix_auto_mail.cli.commands_board.init_db", return_value=conn),
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
        mock.patch("robotsix_auto_mail.cli.commands_board.init_db", return_value=conn),
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
        mock.patch("robotsix_auto_mail.cli.commands_board.init_db", return_value=conn),
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
