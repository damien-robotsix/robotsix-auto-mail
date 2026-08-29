"""Unit tests for compose-draft IMAP APPEND behaviour.

Composing (reply or new) writes a genuine RFC822 message directly into
the account's real IMAP Drafts folder with the ``\\Draft`` flag —
including every file-hub attachment as a MIME part and correct threading
headers for replies.  If the Drafts folder is missing or the APPEND
fails the request fails loudly (502); a stripped or lost draft is never
silently accepted.
"""

from __future__ import annotations

import base64
import json
from unittest import mock

from robotsix_auto_mail.db import MailRecord, init_db, insert_record
from robotsix_auto_mail.imap.mailbox import MailboxInfo
from tests.server.test_compose_draft_mixin import (
    _ComposeDraftFakeHandler,
    _make_accounts,
    _set_json_body,
)


def _drafts_folder() -> list[MailboxInfo]:
    return [
        MailboxInfo(name="INBOX", attributes=("\\HasNoChildren",), delimiter="/"),
        MailboxInfo(
            name="[Gmail]/Drafts",
            attributes=("\\Drafts", "\\HasNoChildren"),
            delimiter="/",
        ),
    ]


def _mock_imap(folders: list[MailboxInfo]) -> mock.MagicMock:
    imap = mock.MagicMock()
    imap.list_folders.return_value = folders
    imap.append_message.return_value = 42
    return imap


class TestComposeDraftImapAppend:
    def test_no_attachments_appends_to_drafts(self, single_db: str) -> None:
        handler = _ComposeDraftFakeHandler(accounts=_make_accounts(db_path=single_db))
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "to": "ext@other.com",
                "subject": "Test",
                "body": "Hello there",
            },
        )
        imap = _mock_imap(_drafts_folder())
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_cls.return_value.__enter__ = mock.MagicMock(return_value=imap)
            mock_cls.return_value.__exit__ = mock.MagicMock(return_value=False)
            handler._handle_compose_draft()

        imap.append_message.assert_called_once()
        call_args = imap.append_message.call_args
        assert call_args[0][0] == "[Gmail]/Drafts"
        assert call_args[1]["flags"] == "(\\Draft)"
        msg_bytes = call_args[0][1]
        assert b"From: user@example.com" in msg_bytes
        assert b"To: ext@other.com" in msg_bytes
        assert b"Subject: Test" in msg_bytes
        handler._serve_json.assert_called_once()
        assert handler._serve_json.call_args[1]["status"] == 201

    def test_reply_sets_threading_headers(self, single_db: str) -> None:
        conn = init_db(single_db, skip_migrations=True)
        try:
            insert_record(
                conn,
                MailRecord(
                    message_id="<orig@example.com>",
                    sender="peer@example.com",
                    subject="Invoice",
                    date="2026-01-01T00:00:00Z",
                    recipients_json=json.dumps({"to": ["user@example.com"], "cc": []}),
                ),
            )
        finally:
            conn.close()

        handler = _ComposeDraftFakeHandler(accounts=_make_accounts(db_path=single_db))
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "body": "My reply.",
                "reply_to_message_id": "<orig@example.com>",
            },
        )
        imap = _mock_imap(_drafts_folder())
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_cls.return_value.__enter__ = mock.MagicMock(return_value=imap)
            mock_cls.return_value.__exit__ = mock.MagicMock(return_value=False)
            handler._handle_compose_draft()

        msg_bytes = imap.append_message.call_args[0][1]
        assert b"In-Reply-To: <orig@example.com>" in msg_bytes
        assert b"References: <orig@example.com>" in msg_bytes
        assert b"Subject: Re: Invoice" in msg_bytes
        assert b"To: peer@example.com" in msg_bytes

    def test_with_attachment_is_base64_multipart(self, single_db: str) -> None:
        handler = _ComposeDraftFakeHandler(accounts=_make_accounts(db_path=single_db))
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "to": "ext@other.com",
                "subject": "With PDF",
                "body": "See attachment.",
                "attachments": ["att-id-1"],
            },
        )
        meta_resp = mock.MagicMock()
        meta_resp.status_code = 200
        meta_resp.json.return_value = {"filename": "report.pdf"}
        download_resp = mock.MagicMock()
        download_resp.status_code = 200
        download_resp.content = b"%PDF-1.4 fake content"

        imap = _mock_imap(_drafts_folder())
        with (
            mock.patch(
                "robotsix_auto_mail.server._compose_draft_mixin.httpx"
            ) as mock_httpx,
            mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
        ):
            http = mock.MagicMock()
            http.__enter__ = mock.MagicMock(return_value=http)
            http.__exit__ = mock.MagicMock(return_value=False)
            http.get.side_effect = [meta_resp, download_resp]
            mock_httpx.Client.return_value = http
            mock_cls.return_value.__enter__ = mock.MagicMock(return_value=imap)
            mock_cls.return_value.__exit__ = mock.MagicMock(return_value=False)
            handler._handle_compose_draft()

        msg_bytes = imap.append_message.call_args[0][1]
        assert b"report.pdf" in msg_bytes
        assert base64.b64encode(b"%PDF-1.4 fake content") in msg_bytes
        assert b"base64" in msg_bytes
        assert b"multipart" in msg_bytes

    def test_no_drafts_folder_fails_loudly(self, single_db: str) -> None:
        handler = _ComposeDraftFakeHandler(accounts=_make_accounts(db_path=single_db))
        _set_json_body(
            handler,
            {"account": "TEST", "to": "ext@other.com", "subject": "T", "body": "H"},
        )
        imap = _mock_imap(
            [MailboxInfo(name="INBOX", attributes=("\\HasNoChildren",), delimiter="/")]
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_cls.return_value.__enter__ = mock.MagicMock(return_value=imap)
            mock_cls.return_value.__exit__ = mock.MagicMock(return_value=False)
            handler._handle_compose_draft()

        imap.append_message.assert_not_called()
        handler._serve_json.assert_not_called()
        handler._send_response.assert_called_once()
        assert handler._send_response.call_args[1]["status"] == 502

    def test_imap_append_failure_fails_loudly(self, single_db: str) -> None:
        handler = _ComposeDraftFakeHandler(accounts=_make_accounts(db_path=single_db))
        _set_json_body(
            handler,
            {"account": "TEST", "to": "ext@other.com", "subject": "T", "body": "H"},
        )
        imap = _mock_imap(_drafts_folder())
        imap.append_message.side_effect = Exception("IMAP error")
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_cls.return_value.__enter__ = mock.MagicMock(return_value=imap)
            mock_cls.return_value.__exit__ = mock.MagicMock(return_value=False)
            handler._handle_compose_draft()

        handler._serve_json.assert_not_called()
        handler._send_response.assert_called_once()
        assert handler._send_response.call_args[1]["status"] == 502
