"""Unit tests for compose-draft IMAP APPEND and send behaviour.

Covers the two acceptance-criteria defects:
- Defect 1: compose-draft sends as a new message (no self-reply guard,
  no reply headers, no "Re:" prefix).
- Defect 2: compose-draft APPENDs the MIME message into the account's
  real IMAP Drafts folder with the ``\\Draft`` flag.
"""

from __future__ import annotations

import json
from unittest import mock

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.imap.mailbox import MailboxInfo
from robotsix_auto_mail.server._compose_draft_mixin import _ComposeDraftMixin
from tests.server._test_helpers import _DraftMixinFakeHandler
from tests.server.conftest_helpers import _seed_draft_record
from tests.server.test_compose_draft_mixin import (
    _ComposeDraftFakeHandler,
    _make_accounts,
    _make_config,
    _set_json_body,
)


# ---------------------------------------------------------------------------
# Defect 1 — compose-draft sends as a new message, not a reply
# ---------------------------------------------------------------------------


class TestComposeDraftSendNotAReply:
    """Sending a compose-draft record must not trigger the self-reply guard
    and must not add reply headers or "Re:" prefix."""

    def test_send_compose_draft_to_external_recipient(
        self, single_db: str
    ) -> None:
        """A compose-draft to an external address sends without self-reply error."""
        _seed_draft_record(
            single_db,
            "<compose-abc123@robotsix-auto-mail>",
            sender="me@example.com",
            subject="Hello",
            draft_text="Draft body",
            recipients_json=json.dumps({"to": ["ext@other.com"], "cc": []}),
        )
        handler = _DraftMixinFakeHandler(
            single_db,
            mail_config=MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="me@example.com",
                password="s3cret",
            ),
        )
        handler.headers.get.return_value = 200
        handler.rfile.read.return_value = (
            b"message_id=<compose-abc123@robotsix-auto-mail>"
            b"&reply_mode=reply&redirect_to=/board"
        )

        with (
            mock.patch("robotsix_auto_mail.smtp.SmtpClient") as mock_smtp_cls,
            mock.patch("robotsix_auto_mail.db.update_sent_reply_text"),
            mock.patch("robotsix_auto_mail.triage.delete_triage_decision"),
        ):
            mock_client = mock_smtp_cls.return_value.__enter__.return_value
            handler._handle_send_draft()

        # No self-reply guard fired — send was called.
        handler._bad_request.assert_not_called()
        mock_client.send.assert_called_once()
        send_kwargs = mock_client.send.call_args[1]
        assert send_kwargs["to_addr"] == "ext@other.com"
        assert send_kwargs["from_addr"] == "me@example.com"
        # No reply headers.
        assert send_kwargs["in_reply_to"] is None
        assert send_kwargs["references"] is None

    def test_send_compose_draft_subject_not_prefixed(self, single_db: str) -> None:
        """Subject is kept as-is (no 'Re:' prefix) for compose-draft records."""
        _seed_draft_record(
            single_db,
            "<compose-def456@robotsix-auto-mail>",
            sender="me@example.com",
            subject="Invoice attached",
            draft_text="Here it is.",
            recipients_json=json.dumps({"to": ["ext@other.com"], "cc": []}),
        )
        handler = _DraftMixinFakeHandler(
            single_db,
            mail_config=MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="me@example.com",
                password="s3cret",
            ),
        )
        handler.headers.get.return_value = 200
        handler.rfile.read.return_value = (
            b"message_id=<compose-def456@robotsix-auto-mail>"
            b"&reply_mode=reply&redirect_to=/board"
        )

        with (
            mock.patch("robotsix_auto_mail.smtp.SmtpClient") as mock_smtp_cls,
            mock.patch("robotsix_auto_mail.db.update_sent_reply_text"),
            mock.patch("robotsix_auto_mail.triage.delete_triage_decision"),
        ):
            mock_client = mock_smtp_cls.return_value.__enter__.return_value
            handler._handle_send_draft()

        assert mock_client.send.call_args[1]["subject"] == "Invoice attached"

    def test_send_compose_draft_with_cc(self, single_db: str) -> None:
        """CC recipients from the compose-draft record are forwarded."""
        _seed_draft_record(
            single_db,
            "<compose-ghi789@robotsix-auto-mail>",
            sender="me@example.com",
            subject="Report",
            draft_text="See attached.",
            recipients_json=json.dumps(
                {"to": ["ext@other.com"], "cc": ["cc@other.com"]}
            ),
        )
        handler = _DraftMixinFakeHandler(
            single_db,
            mail_config=MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="me@example.com",
                password="s3cret",
            ),
        )
        handler.headers.get.return_value = 200
        handler.rfile.read.return_value = (
            b"message_id=<compose-ghi789@robotsix-auto-mail>"
            b"&reply_mode=reply&redirect_to=/board"
        )

        with (
            mock.patch("robotsix_auto_mail.smtp.SmtpClient") as mock_smtp_cls,
            mock.patch("robotsix_auto_mail.db.update_sent_reply_text"),
            mock.patch("robotsix_auto_mail.triage.delete_triage_decision"),
        ):
            mock_client = mock_smtp_cls.return_value.__enter__.return_value
            handler._handle_send_draft()

        send_kwargs = mock_client.send.call_args[1]
        assert send_kwargs["cc"] == ["cc@other.com"]


# ---------------------------------------------------------------------------
# Defect 2 — IMAP APPEND into Drafts folder
# ---------------------------------------------------------------------------


class TestComposeDraftImapAppend:
    """Compose-draft must APPEND the MIME message to the IMAP Drafts folder."""

    def test_no_attachments_appends_to_drafts(
        self, single_db: str
    ) -> None:
        """A compose-draft with no attachments APPENDs a plain text message."""
        accounts = _make_accounts(db_path=single_db)
        handler = _ComposeDraftFakeHandler(accounts=accounts)
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "to": "ext@other.com",
                "subject": "Test",
                "body": "Hello there",
            },
        )

        mock_folders = [
            MailboxInfo(
                name="INBOX", attributes=("\\HasNoChildren",), delimiter="/"
            ),
            MailboxInfo(
                name="[Gmail]/Drafts",
                attributes=("\\Drafts", "\\HasNoChildren"),
                delimiter="/",
            ),
        ]
        mock_imap = mock.MagicMock()
        mock_imap.list_folders.return_value = mock_folders
        mock_imap.append_message = mock.MagicMock()

        with mock.patch(
            "robotsix_auto_mail.imap.ImapClient"
        ) as mock_cls:
            mock_cls.return_value.__enter__ = mock.MagicMock(
                return_value=mock_imap
            )
            mock_cls.return_value.__exit__ = mock.MagicMock(return_value=False)
            handler._handle_compose_draft()

        mock_imap.append_message.assert_called_once()
        call_args = mock_imap.append_message.call_args
        assert call_args[0][0] == "[Gmail]/Drafts"
        assert call_args[1]["flags"] == "(\\Draft)"
        # The message bytes should be a valid MIME message.
        msg_bytes = call_args[0][1]
        assert b"From: user@example.com" in msg_bytes
        assert b"To: ext@other.com" in msg_bytes
        assert b"Subject: Test" in msg_bytes

    def test_with_attachments_appends_multipart(
        self, single_db: str
    ) -> None:
        """With attachments, APPENDs a multipart MIME message."""
        accounts = _make_accounts(db_path=single_db)
        handler = _ComposeDraftFakeHandler(accounts=accounts)
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

        mock_folders = [
            MailboxInfo(
                name="Drafts",
                attributes=("\\Drafts", "\\HasNoChildren"),
                delimiter="/",
            ),
        ]
        mock_imap = mock.MagicMock()
        mock_imap.list_folders.return_value = mock_folders

        # Mock httpx: first call for metadata, second for download.
        meta_resp = mock.MagicMock()
        meta_resp.status_code = 200
        meta_resp.json.return_value = {
            "id": "att-id-1",
            "filename": "report.pdf",
            "content_type": "application/pdf",
            "size": 100,
        }

        download_resp = mock.MagicMock()
        download_resp.status_code = 200
        download_resp.content = b"%PDF-1.4 fake content"

        with (
            mock.patch(
                "robotsix_auto_mail.server._compose_draft_mixin.httpx"
            ) as mock_httpx,
            mock.patch(
                "robotsix_auto_mail.imap.ImapClient"
            ) as mock_imap_cls,
        ):
            mock_http_client = mock.MagicMock()
            mock_http_client.__enter__ = mock.MagicMock(
                return_value=mock_http_client
            )
            mock_http_client.__exit__ = mock.MagicMock(return_value=False)
            # First call: metadata. Second call: download.
            mock_http_client.get.side_effect = [meta_resp, download_resp]
            mock_httpx.Client.return_value = mock_http_client

            mock_imap_cls.return_value.__enter__ = mock.MagicMock(
                return_value=mock_imap
            )
            mock_imap_cls.return_value.__exit__ = mock.MagicMock(
                return_value=False
            )
            handler._handle_compose_draft()

        mock_imap.append_message.assert_called_once()
        msg_bytes = mock_imap.append_message.call_args[0][1]
        assert b"report.pdf" in msg_bytes
        # Attachment is base64-encoded (not raw bytes).
        import base64
        assert base64.b64encode(b"%PDF-1.4 fake content") in msg_bytes
        assert b"base64" in msg_bytes
        assert b"multipart" in msg_bytes

    def test_no_drafts_folder_logs_warning(
        self, single_db: str
    ) -> None:
        """When no Drafts folder is found, logs a warning (doesn't fail)."""
        accounts = _make_accounts(db_path=single_db)
        handler = _ComposeDraftFakeHandler(accounts=accounts)
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "to": "ext@other.com",
                "subject": "Test",
                "body": "Hello",
            },
        )

        # No folder with \Drafts attribute.
        mock_folders = [
            MailboxInfo(
                name="INBOX", attributes=("\\HasNoChildren",), delimiter="/"
            ),
        ]
        mock_imap = mock.MagicMock()
        mock_imap.list_folders.return_value = mock_folders

        with mock.patch(
            "robotsix_auto_mail.imap.ImapClient"
        ) as mock_imap_cls:
            mock_imap_cls.return_value.__enter__ = mock.MagicMock(
                return_value=mock_imap
            )
            mock_imap_cls.return_value.__exit__ = mock.MagicMock(
                return_value=False
            )
            handler._handle_compose_draft()

        # Should not crash — append_message was never called.
        mock_imap.append_message.assert_not_called()
        # But the board card should still be created.
        handler._serve_json.assert_called_once()
        assert handler._serve_json.call_args[1]["status"] == 201

    def test_imap_append_failure_does_not_fail_request(
        self, single_db: str
    ) -> None:
        """If IMAP APPEND fails, the request still succeeds (board card)."""
        accounts = _make_accounts(db_path=single_db)
        handler = _ComposeDraftFakeHandler(accounts=accounts)
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "to": "ext@other.com",
                "subject": "Test",
                "body": "Hello",
            },
        )

        mock_folders = [
            MailboxInfo(
                name="Drafts",
                attributes=("\\Drafts", "\\HasNoChildren"),
                delimiter="/",
            ),
        ]
        mock_imap = mock.MagicMock()
        mock_imap.list_folders.return_value = mock_folders
        mock_imap.append_message.side_effect = Exception("IMAP error")

        with mock.patch(
            "robotsix_auto_mail.imap.ImapClient"
        ) as mock_imap_cls:
            mock_imap_cls.return_value.__enter__ = mock.MagicMock(
                return_value=mock_imap
            )
            mock_imap_cls.return_value.__exit__ = mock.MagicMock(
                return_value=False
            )
            handler._handle_compose_draft()

        # Request still succeeds with 201.
        handler._serve_json.assert_called_once()
        assert handler._serve_json.call_args[1]["status"] == 201