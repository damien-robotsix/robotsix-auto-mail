"""Cross-account resolution tests for ``_handle_delete``.

A compose-draft created via ``POST /compose-draft`` for a specific
account is stored in *that* account's DB.  A programmatic ``POST
/delete`` that carries only the synthetic
``<compose-...@robotsix-auto-mail>`` message id (no ``?account=`` hint
and no cookie) resolves to the *first* configured account, so the
delete must fall back to searching every configured account instead of
404-ing.
"""

from __future__ import annotations

from unittest import mock

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.db import get_record_by_message_id, init_db
from tests.server._test_helpers import _FakeHandler
from tests.server.conftest_helpers import _populate_db


def _make_config(db_path: str) -> MailConfig:
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        db_path=db_path,
    )


def _make_accounts(db_a: str, db_b: str) -> MailAccountsConfig:
    return MailAccountsConfig(
        accounts=[
            MailAccount(account_id="A", config=_make_config(db_a)),
            MailAccount(account_id="B", config=_make_config(db_b)),
        ]
    )


def test_delete_resolves_compose_draft_in_other_account(tmp_path: object) -> None:
    """A compose-draft owned by the non-selected account is deletable."""
    db_a = str(tmp_path / "a.db")  # type: ignore[operator]
    db_b = str(tmp_path / "b.db")  # type: ignore[operator]
    init_db(db_a).close()
    msg_id = "<compose-deadbeefdeadbeef@robotsix-auto-mail>"
    _populate_db(
        db_b,
        [
            {
                "message_id": msg_id,
                "sender": "user@example.com",
                "subject": "Draft",
                "date": "2026-08-28T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    accounts = _make_accounts(db_a, db_b)
    # Currently-selected account is A (the first / default), but the
    # draft lives in account B's DB.
    handler = _FakeHandler(db_a, mail_config=_make_config(db_a))
    handler.accounts = accounts
    handler.headers.get.return_value = 200
    handler.rfile.read.return_value = f"message_id={msg_id}&redirect_to=/board".encode()

    with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
        mock_client = mock_cls.return_value.__enter__.return_value
        mock_client.list_folders.return_value = []
        handler._handle_delete()

    handler._not_found.assert_not_called()
    handler._redirect.assert_called_once()

    conn = init_db(db_b)
    try:
        assert get_record_by_message_id(conn, msg_id) is None
    finally:
        conn.close()


def test_delete_unknown_id_returns_404(tmp_path: object) -> None:
    """A genuinely-unknown id still 404s after searching every account."""
    db_a = str(tmp_path / "a.db")  # type: ignore[operator]
    db_b = str(tmp_path / "b.db")  # type: ignore[operator]
    init_db(db_a).close()
    init_db(db_b).close()

    accounts = _make_accounts(db_a, db_b)
    handler = _FakeHandler(db_a, mail_config=_make_config(db_a))
    handler.accounts = accounts
    handler.headers.get.return_value = 200
    handler.rfile.read.return_value = (
        b"message_id=<compose-missing@robotsix-auto-mail>&redirect_to=/board"
    )

    handler._handle_delete()

    handler._not_found.assert_called_once()
    handler._redirect.assert_not_called()
