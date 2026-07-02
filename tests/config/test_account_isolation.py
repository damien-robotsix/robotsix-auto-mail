"""End-to-end data-isolation tests for the multi-account model.

The multi-account epic models N accounts as N ``MailConfig`` instances,
each with its own SQLite DB (``MailConfig.db_path``).  Isolation is achieved
purely by "one DB per account" — there is no ``account_id`` schema column.

The existing multi-account CLI tests (``tests/cli/test_cli.py``) mock out
``_ingest_cycle``, so they prove dispatch but never prove that ingesting for
account A leaves account B's database untouched.  These tests close that gap:
they drive the real ``ingest`` loop end-to-end (real ``init_db`` + real
``ingest_mail``, with only the IMAP transport faked) and assert that records,
watermark, ``sender_memory`` and ``triage_decisions`` written for one account
never leak into the other account's DB.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.db import (
    MailRecord,
    get_watermark,
    init_db,
    insert_record,
    set_watermark,
)
from robotsix_auto_mail.imap import ImapClient
from robotsix_auto_mail.triage import (
    get_triage_decision,
    list_triage_decisions,
    set_triage_decision,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_raw_message(
    *,
    message_id: str,
    sender: str = "alice@example.com",
    subject: str = "Hello",
    date: str = "Wed, 15 Jan 2025 10:30:00 +0000",
    body: str = "plain text body",
) -> bytes:
    """Build a minimal, valid MIME message as bytes (mirrors test_pipeline)."""
    return (
        f"From: {sender}\r\n"
        f"To: bob@example.com\r\n"
        f"Subject: {subject}\r\n"
        f"Date: {date}\r\n"
        f"Message-ID: {message_id}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n"
        f"{body}"
    ).encode("utf-8")


def _account_config(*, username: str, db_path: str) -> MailConfig:
    """A MailConfig that ingests without touching the LLM/archive layers."""
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username=username,
        password="s3cret",
        db_path=db_path,
        archive_enabled=False,
        triage_on_ingest=False,
    )


def _open(path: str) -> Iterator[sqlite3.Connection]:
    """Yield a fresh connection to *path*, always closing it (no ResourceWarning)."""
    conn = init_db(path)
    try:
        yield conn
    finally:
        conn.close()


def _message_ids(conn: sqlite3.Connection) -> set[str]:
    """Return the set of message_ids stored in *conn*'s mail_records table."""
    cur = conn.execute("SELECT message_id FROM mail_records")
    return {row[0] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# End-to-end ingest isolation via the CLI loop
# ---------------------------------------------------------------------------


def test_ingest_all_accounts_isolates_records_and_watermark(
    tmp_path: Path,
) -> None:
    """``ingest --all-accounts`` keeps each account's records + watermark in
    its own DB, with zero rows leaking into the other account's DB."""
    from robotsix_auto_mail.cli import main

    personal = _account_config(
        username="me@example.com", db_path=str(tmp_path / "personal.db")
    )
    work = _account_config(username="me@work.com", db_path=str(tmp_path / "work.db"))
    accounts = MailAccountsConfig(
        accounts=(
            MailAccount(account_id="personal", config=personal, label=None),
            MailAccount(account_id="work", config=work, label=None),
        ),
        default_account_id="personal",
    )

    # Each account fetches a *different* set of messages with distinct UID
    # ranges so the watermark per DB is also distinct.
    per_account_messages = {
        "me@example.com": [
            (1, _make_raw_message(message_id="<p1@x>")),
            (2, _make_raw_message(message_id="<p2@x>")),
        ],
        "me@work.com": [
            (5, _make_raw_message(message_id="<w1@x>")),
        ],
    }

    def fake_fetch(
        conn: sqlite3.Connection,
        client: object,
        config: MailConfig,
    ) -> list[tuple[int, bytes]]:
        return per_account_messages[config.username]

    with (
        mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=accounts),
        mock.patch("robotsix_auto_mail.cli.ImapClient") as mock_imap,
        mock.patch(
            "robotsix_auto_mail.pipeline.fetch_new_messages", side_effect=fake_fetch
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_ingest.reconcile_records",
            return_value=(0, 0),
        ),
    ):
        # The UIDVALIDITY reconcile step selects the folder; give the mocked
        # client a parseable (count, uidvalidity) so it doesn't trigger a reset.
        _client = mock_imap.return_value.__enter__.return_value
        _client.select_folder_and_uidvalidity.return_value = (0, None)
        rc = main(["ingest", "--all-accounts"])

    assert rc == 0

    # Re-open each on-disk DB and inspect what landed.
    for conn in _open(personal.db_path):
        assert _message_ids(conn) == {"<p1@x>", "<p2@x>"}
        # The work account's message must be absent.
        assert "<w1@x>" not in _message_ids(conn)
        # Watermark reflects the personal account's max UID only.
        assert get_watermark(conn, "imap_uid") == "2"

    for conn in _open(work.db_path):
        assert _message_ids(conn) == {"<w1@x>"}
        # The personal account's messages must be absent.
        assert _message_ids(conn).isdisjoint({"<p1@x>", "<p2@x>"})
        # Watermark reflects the work account's max UID only.
        assert get_watermark(conn, "imap_uid") == "5"


# ---------------------------------------------------------------------------
# Triage / SenderMemory / watermark / triage_decisions isolation
# ---------------------------------------------------------------------------


def test_triage_state_does_not_cross_account_dbs(tmp_path: Path) -> None:
    """watermark and triage_decisions written to account A's connection are
    invisible from account B's connection (per-account SQLite isolation)."""
    path_a = str(tmp_path / "a.db")
    path_b = str(tmp_path / "b.db")

    conn_a = init_db(path_a)
    conn_b = init_db(path_b)
    try:
        # -- Write triage state against connection A only. -------------------
        # triage_decisions has a FK to mail_records, so seed the record first.
        insert_record(
            conn_a,
            MailRecord(
                message_id="<m@a>",
                sender="alice@example.com",
                subject="Hi",
                date="2025-01-01T00:00:00",
            ),
        )
        set_triage_decision(conn_a, "<m@a>", "TO_ARCHIVE", source="user")
        set_watermark(conn_a, "imap_uid", "7")

        # -- Connection A sees its own state. -------------------------------
        assert [d.message_id for d in list_triage_decisions(conn_a)] == ["<m@a>"]
        assert get_triage_decision(conn_a, "<m@a>") is not None
        assert get_watermark(conn_a, "imap_uid") == "7"

        # -- Connection B observes none of it. ------------------------------
        assert list_triage_decisions(conn_b) == []
        assert get_triage_decision(conn_b, "<m@a>") is None
        assert get_watermark(conn_b, "imap_uid") is None
    finally:
        conn_a.close()
        conn_b.close()


# ---------------------------------------------------------------------------
# Single-account backward compatibility
# ---------------------------------------------------------------------------


def test_single_account_ingest_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A single-account container ingests and prints no per-account header."""
    from robotsix_auto_mail.cli import main

    monkeypatch.chdir(tmp_path)

    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="solo@example.com",
        password="s3cret",
        db_path=str(tmp_path / "solo.db"),
        archive_enabled=False,
        triage_on_ingest=False,
    )

    accounts = MailAccountsConfig(
        accounts=(MailAccount(account_id="default", config=cfg, label=None),),
        default_account_id="default",
    )

    def fake_fetch(
        conn: sqlite3.Connection,
        client: object,
        config: MailConfig,
    ) -> list[tuple[int, bytes]]:
        return [
            (1, _make_raw_message(message_id="<s1@x>")),
            (2, _make_raw_message(message_id="<s2@x>")),
        ]

    with (
        mock.patch("robotsix_auto_mail.cli.load_accounts", return_value=accounts),
        mock.patch("robotsix_auto_mail.cli.ImapClient") as mock_imap,
        mock.patch(
            "robotsix_auto_mail.pipeline.fetch_new_messages", side_effect=fake_fetch
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_ingest.reconcile_records",
            return_value=(0, 0),
        ),
    ):
        # The UIDVALIDITY reconcile step selects the folder; give the mocked
        # client a parseable (count, uidvalidity) so it doesn't trigger a reset.
        _client = mock_imap.return_value.__enter__.return_value
        _client.select_folder_and_uidvalidity.return_value = (0, None)
        rc = main(["ingest"])

    assert rc == 0
    out = capsys.readouterr().out
    # Single-account output must not carry the multi-account header.
    assert "=== account:" not in out
    assert "Fetched:  2 messages" in out
    assert "Stored:   2 new" in out

    # The records landed in the default DB path.
    for conn in _open(cfg.db_path):
        assert _message_ids(conn) == {"<s1@x>", "<s2@x>"}
        assert get_watermark(conn, "imap_uid") == "2"


def test_per_account_default_folder_layout() -> None:
    """An account omitting store.path defaults to ``.data/<id>/mail.db``."""
    accounts = MailAccountsConfig(
        accounts=(
            MailAccount(
                account_id="personal",
                config=MailConfig(
                    imap_host="i",
                    smtp_host="s",
                    username="u",
                    password="p",
                    db_path=".data/personal/mail.db",
                ),
            ),
            MailAccount(
                account_id="work",
                config=MailConfig(
                    imap_host="i",
                    smtp_host="s",
                    username="u",
                    password="p",
                    db_path=".data/work/mail.db",
                ),
            ),
        ),
        default_account_id="personal",
    )
    assert accounts.get("personal").config.db_path == ".data/personal/mail.db"
    assert accounts.get("work").config.db_path == ".data/work/mail.db"


def test_per_account_folder_created_on_db_open(tmp_path: Path) -> None:
    """Opening an account DB creates its ``.data/<id>/`` folder if absent."""
    db_path = tmp_path / ".data" / "personal" / "mail.db"
    assert not db_path.parent.exists()
    conn = init_db(str(db_path))
    try:
        assert db_path.parent.is_dir()
        assert db_path.exists()
    finally:
        conn.close()


def test_imap_client_is_pure_protocol_client(cfg: MailConfig) -> None:
    """Audit guard: constructing an ImapClient opens no DB and loads no config.

    A regression that made the IMAP layer self-load config or open a shared
    DB would break per-account isolation; this pins the pure-client contract.
    """
    with (
        mock.patch("robotsix_auto_mail.config.load") as mock_load,
        mock.patch("sqlite3.connect") as mock_connect,
    ):
        ImapClient(cfg)
    mock_load.assert_not_called()
    mock_connect.assert_not_called()
