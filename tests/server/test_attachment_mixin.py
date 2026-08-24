"""Unit tests for ``_AttachmentMixin._handle_push_to_file_hub``.

Covers: file-hub not configured, message not found, no attachments,
single-attachment selection by filename/index, push-all, IMAP errors,
file-hub upload errors, and the happy path.
"""

from __future__ import annotations

import email
import json
from unittest import mock

from robotsix_auto_mail.config import MailAccountsConfig, MailConfig
from robotsix_auto_mail.db import init_db
from robotsix_auto_mail.imap import ImapError
from robotsix_auto_mail.server._attachment_mixin import _AttachmentMixin
from tests.server.conftest_helpers import _populate_db


def _populate_db_with_attachments(
    db_path: str,
    inserts: list[dict[str, str]],
) -> None:
    """Like ``_populate_db`` but honours ``attachments_json`` and ``imap_uid``."""
    conn = init_db(db_path)
    try:
        for row in inserts:
            conn.execute(
                "INSERT INTO mail_records "
                "(message_id, sender, subject, date, recipients_json, "
                "body_plain, body_html, attachments_json, status, "
                "imap_uid, source_folder) "
                "VALUES (?, ?, ?, ?, '{}', ?, '', ?, ?, ?, ?)",
                (
                    row["message_id"],
                    row["sender"],
                    row["subject"],
                    row["date"],
                    row.get("body_plain", ""),
                    row.get("attachments_json", "[]"),
                    row["status"],
                    row.get("imap_uid"),
                    row.get("source_folder", "INBOX"),
                ),
            )
        conn.commit()
    finally:
        conn.close()


class _AttachmentFakeHandler(_AttachmentMixin):
    """Concrete handler wiring ``BoardHandlerProtocol`` attributes to mocks."""

    def __init__(
        self,
        db_path: str,
        mail_config: MailConfig | None = None,
        accounts: MailAccountsConfig | None = None,
    ) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self.accounts = accounts
        self._current_account_id = None
        self._aggregate = False
        self._account_cookie = None
        self.headers = mock.MagicMock()
        self.rfile = mock.MagicMock()
        self._send_response = mock.MagicMock()
        self._redirect = mock.MagicMock()
        self._not_found = mock.MagicMock()
        self._bad_request = mock.MagicMock()


def _make_mime_message(
    attachments: list[tuple[str, str, bytes]],
) -> bytes:
    """Build a minimal MIME message with the given attachments.

    Each attachment is ``(filename, content_type, payload_bytes)``.
    """
    msg = email.message.Message()
    msg["Subject"] = "Test"
    msg["From"] = "sender@example.com"
    msg.set_type("multipart/mixed")
    # Text body part
    text_part = email.message.Message()
    text_part.set_type("text/plain")
    text_part.set_payload("Hello")
    msg.attach(text_part)
    # Attachment parts
    for filename, content_type, payload in attachments:
        part = email.message.Message()
        part.set_type(content_type)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        part.set_payload(payload)
        msg.attach(part)
    return msg.as_bytes()


def _make_accounts(file_hub_url: str = "http://file-hub:8080") -> MailAccountsConfig:
    """Build a minimal ``MailAccountsConfig`` with the given file_hub_url."""
    return MailAccountsConfig(
        accounts=[],
        file_hub_url=file_hub_url,
    )


_ATTACHMENTS_JSON = json.dumps(
    [
        {"filename": "report.pdf", "mime_type": "application/pdf", "size": 100},
        {"filename": "data.csv", "mime_type": "text/csv", "size": 50},
    ]
)


class TestPushToFileHubNotConfigured:
    def test_no_accounts(self, single_db: str) -> None:
        handler = _AttachmentFakeHandler(single_db, accounts=None)
        handler._handle_push_to_file_hub("<msg@example.com>")
        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 503

    def test_empty_file_hub_url(self, single_db: str) -> None:
        accounts = _make_accounts(file_hub_url="")
        handler = _AttachmentFakeHandler(single_db, accounts=accounts)
        handler._handle_push_to_file_hub("<msg@example.com>")
        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 503


class TestPushToFileHubMessageNotFound:
    def test_unknown_message_id(self, single_db: str) -> None:
        accounts = _make_accounts()
        handler = _AttachmentFakeHandler(single_db, accounts=accounts)
        handler.headers.get.return_value = 0
        handler._handle_push_to_file_hub("<nonexistent@example.com>")
        handler._not_found.assert_called_once()


class TestPushToFileHubNoAttachments:
    def test_message_with_no_attachments(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "<no-att@example.com>",
                    "sender": "x@x.com",
                    "subject": "No attachments",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        accounts = _make_accounts()
        handler = _AttachmentFakeHandler(single_db, accounts=accounts)
        handler.headers.get.return_value = 0
        handler._handle_push_to_file_hub("<no-att@example.com>")
        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 400
        body = json.loads(args[0][0])
        assert "no attachments" in body["error"].lower()


class TestPushToFileHubSelection:
    def test_filename_not_found(self, single_db: str) -> None:
        _populate_db_with_attachments(
            single_db,
            [
                {
                    "message_id": "<sel@example.com>",
                    "sender": "x@x.com",
                    "subject": "Has attachments",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                    "attachments_json": _ATTACHMENTS_JSON,
                },
            ],
        )
        accounts = _make_accounts()
        handler = _AttachmentFakeHandler(single_db, accounts=accounts)
        handler.headers.get.return_value = 50
        handler.rfile.read.return_value = json.dumps(
            {"filename": "nonexistent.pdf"}
        ).encode()
        handler._handle_push_to_file_hub("<sel@example.com>")
        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 404

    def test_index_out_of_range(self, single_db: str) -> None:
        _populate_db_with_attachments(
            single_db,
            [
                {
                    "message_id": "<idx@example.com>",
                    "sender": "x@x.com",
                    "subject": "Has attachments",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                    "attachments_json": _ATTACHMENTS_JSON,
                },
            ],
        )
        accounts = _make_accounts()
        handler = _AttachmentFakeHandler(single_db, accounts=accounts)
        handler.headers.get.return_value = 20
        handler.rfile.read.return_value = json.dumps({"index": 99}).encode()
        handler._handle_push_to_file_hub("<idx@example.com>")
        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 400

    def test_invalid_body(self, single_db: str) -> None:
        _populate_db_with_attachments(
            single_db,
            [
                {
                    "message_id": "<bad@example.com>",
                    "sender": "x@x.com",
                    "subject": "Has attachments",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                    "attachments_json": _ATTACHMENTS_JSON,
                },
            ],
        )
        accounts = _make_accounts()
        handler = _AttachmentFakeHandler(single_db, accounts=accounts)
        handler.headers.get.return_value = 5
        handler.rfile.read.return_value = b"not-json"
        handler._handle_push_to_file_hub("<bad@example.com>")
        handler._bad_request.assert_called_once()


class TestPushToFileHubImapErrors:
    def test_no_mail_config(self, single_db: str) -> None:
        _populate_db_with_attachments(
            single_db,
            [
                {
                    "message_id": "<noimap@example.com>",
                    "sender": "x@x.com",
                    "subject": "Has attachments",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                    "attachments_json": _ATTACHMENTS_JSON,
                    "imap_uid": "42",
                },
            ],
        )
        accounts = _make_accounts()
        handler = _AttachmentFakeHandler(
            single_db, mail_config=None, accounts=accounts
        )
        handler.headers.get.return_value = 0
        handler._handle_push_to_file_hub("<noimap@example.com>")
        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 502

    def test_no_imap_uid(self, single_db: str) -> None:
        _populate_db_with_attachments(
            single_db,
            [
                {
                    "message_id": "<nouid@example.com>",
                    "sender": "x@x.com",
                    "subject": "Has attachments",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                    "attachments_json": _ATTACHMENTS_JSON,
                },
            ],
        )
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        accounts = _make_accounts()
        handler = _AttachmentFakeHandler(
            single_db, mail_config=mail_config, accounts=accounts
        )
        handler.headers.get.return_value = 0
        handler._handle_push_to_file_hub("<nouid@example.com>")
        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 502

    def test_imap_fetch_error(self, single_db: str) -> None:
        _populate_db_with_attachments(
            single_db,
            [
                {
                    "message_id": "<imaperr@example.com>",
                    "sender": "x@x.com",
                    "subject": "Has attachments",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                    "attachments_json": _ATTACHMENTS_JSON,
                    "imap_uid": "42",
                    "source_folder": "INBOX",
                },
            ],
        )
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        accounts = _make_accounts()
        handler = _AttachmentFakeHandler(
            single_db, mail_config=mail_config, accounts=accounts
        )
        handler.headers.get.return_value = 0

        with mock.patch(
            "robotsix_auto_mail.imap.ImapClient"
        ) as mock_cls:
            mock_client = mock.MagicMock()
            mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
            mock_client.__exit__ = mock.MagicMock(return_value=False)
            mock_client.fetch_messages.side_effect = ImapError("IMAP down")
            mock_cls.return_value = mock_client

            handler._handle_push_to_file_hub("<imaperr@example.com>")

        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 502


class TestPushToFileHubHappyPath:
    def test_push_all_attachments(self, single_db: str) -> None:
        _populate_db_with_attachments(
            single_db,
            [
                {
                    "message_id": "<happy@example.com>",
                    "sender": "x@x.com",
                    "subject": "Has attachments",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                    "attachments_json": _ATTACHMENTS_JSON,
                    "imap_uid": "42",
                    "source_folder": "INBOX",
                },
            ],
        )
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        accounts = _make_accounts()
        handler = _AttachmentFakeHandler(
            single_db, mail_config=mail_config, accounts=accounts
        )
        handler.headers.get.return_value = 0

        raw_email = _make_mime_message(
            [
                ("report.pdf", "application/pdf", b"%PDF-1.4 fake"),
                ("data.csv", "text/csv", b"a,b,c\n1,2,3"),
            ]
        )

        file_hub_response = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "filename": "report.pdf",
            "size": 100,
            "content_type": "application/pdf",
        }

        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_imap_cls,
            mock.patch(
                "robotsix_auto_mail.server._attachment_mixin.httpx"
            ) as mock_httpx,
        ):
            mock_imap = mock.MagicMock()
            mock_imap.__enter__ = mock.MagicMock(return_value=mock_imap)
            mock_imap.__exit__ = mock.MagicMock(return_value=False)
            mock_imap.fetch_messages.return_value = [(42, raw_email)]
            mock_imap_cls.return_value = mock_imap

            mock_httpx_client = mock.MagicMock()
            mock_httpx_client.__enter__ = mock.MagicMock(
                return_value=mock_httpx_client
            )
            mock_httpx_client.__exit__ = mock.MagicMock(return_value=False)
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = file_hub_response
            mock_httpx_client.post.return_value = mock_resp
            mock_httpx.Client.return_value = mock_httpx_client

            handler._handle_push_to_file_hub("<happy@example.com>")

        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 200
        body = json.loads(args[0][0])
        assert len(body["attachments"]) == 2
        # Verify file-hub was called twice (once per attachment)
        assert mock_httpx_client.post.call_count == 2

    def test_push_single_by_filename(self, single_db: str) -> None:
        _populate_db_with_attachments(
            single_db,
            [
                {
                    "message_id": "<single@example.com>",
                    "sender": "x@x.com",
                    "subject": "Has attachments",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                    "attachments_json": _ATTACHMENTS_JSON,
                    "imap_uid": "42",
                    "source_folder": "INBOX",
                },
            ],
        )
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        accounts = _make_accounts()
        handler = _AttachmentFakeHandler(
            single_db, mail_config=mail_config, accounts=accounts
        )
        handler.headers.get.return_value = 40
        handler.rfile.read.return_value = json.dumps(
            {"filename": "report.pdf"}
        ).encode()

        raw_email = _make_mime_message(
            [
                ("report.pdf", "application/pdf", b"%PDF-1.4 fake"),
                ("data.csv", "text/csv", b"a,b,c\n1,2,3"),
            ]
        )

        file_hub_response = {
            "id": "550e8400-e29b-41d4-a716-446655440001",
            "filename": "report.pdf",
            "size": 100,
            "content_type": "application/pdf",
        }

        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_imap_cls,
            mock.patch(
                "robotsix_auto_mail.server._attachment_mixin.httpx"
            ) as mock_httpx,
        ):
            mock_imap = mock.MagicMock()
            mock_imap.__enter__ = mock.MagicMock(return_value=mock_imap)
            mock_imap.__exit__ = mock.MagicMock(return_value=False)
            mock_imap.fetch_messages.return_value = [(42, raw_email)]
            mock_imap_cls.return_value = mock_imap

            mock_httpx_client = mock.MagicMock()
            mock_httpx_client.__enter__ = mock.MagicMock(
                return_value=mock_httpx_client
            )
            mock_httpx_client.__exit__ = mock.MagicMock(return_value=False)
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = file_hub_response
            mock_httpx_client.post.return_value = mock_resp
            mock_httpx.Client.return_value = mock_httpx_client

            handler._handle_push_to_file_hub("<single@example.com>")

        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 200
        body = json.loads(args[0][0])
        assert len(body["attachments"]) == 1
        # Only one upload call
        assert mock_httpx_client.post.call_count == 1

    def test_push_single_by_index(self, single_db: str) -> None:
        _populate_db_with_attachments(
            single_db,
            [
                {
                    "message_id": "<byidx@example.com>",
                    "sender": "x@x.com",
                    "subject": "Has attachments",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                    "attachments_json": _ATTACHMENTS_JSON,
                    "imap_uid": "42",
                    "source_folder": "INBOX",
                },
            ],
        )
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        accounts = _make_accounts()
        handler = _AttachmentFakeHandler(
            single_db, mail_config=mail_config, accounts=accounts
        )
        handler.headers.get.return_value = 15
        handler.rfile.read.return_value = json.dumps({"index": 1}).encode()

        raw_email = _make_mime_message(
            [
                ("report.pdf", "application/pdf", b"%PDF-1.4 fake"),
                ("data.csv", "text/csv", b"a,b,c\n1,2,3"),
            ]
        )

        file_hub_response = {
            "id": "550e8400-e29b-41d4-a716-446655440002",
            "filename": "data.csv",
            "size": 50,
            "content_type": "text/csv",
        }

        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_imap_cls,
            mock.patch(
                "robotsix_auto_mail.server._attachment_mixin.httpx"
            ) as mock_httpx,
        ):
            mock_imap = mock.MagicMock()
            mock_imap.__enter__ = mock.MagicMock(return_value=mock_imap)
            mock_imap.__exit__ = mock.MagicMock(return_value=False)
            mock_imap.fetch_messages.return_value = [(42, raw_email)]
            mock_imap_cls.return_value = mock_imap

            mock_httpx_client = mock.MagicMock()
            mock_httpx_client.__enter__ = mock.MagicMock(
                return_value=mock_httpx_client
            )
            mock_httpx_client.__exit__ = mock.MagicMock(return_value=False)
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = file_hub_response
            mock_httpx_client.post.return_value = mock_resp
            mock_httpx.Client.return_value = mock_httpx_client

            handler._handle_push_to_file_hub("<byidx@example.com>")

        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 200
        body = json.loads(args[0][0])
        assert len(body["attachments"]) == 1
        assert body["attachments"][0]["filename"] == "data.csv"


class TestPushToFileHubFileHubErrors:
    def test_file_hub_connection_error(self, single_db: str) -> None:
        _populate_db_with_attachments(
            single_db,
            [
                {
                    "message_id": "<fherr@example.com>",
                    "sender": "x@x.com",
                    "subject": "Has attachments",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                    "attachments_json": _ATTACHMENTS_JSON,
                    "imap_uid": "42",
                    "source_folder": "INBOX",
                },
            ],
        )
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        accounts = _make_accounts()
        handler = _AttachmentFakeHandler(
            single_db, mail_config=mail_config, accounts=accounts
        )
        handler.headers.get.return_value = 0

        raw_email = _make_mime_message(
            [("report.pdf", "application/pdf", b"%PDF-1.4 fake")]
        )

        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_imap_cls,
            mock.patch(
                "robotsix_auto_mail.server._attachment_mixin.httpx"
            ) as mock_httpx,
        ):
            mock_imap = mock.MagicMock()
            mock_imap.__enter__ = mock.MagicMock(return_value=mock_imap)
            mock_imap.__exit__ = mock.MagicMock(return_value=False)
            mock_imap.fetch_messages.return_value = [(42, raw_email)]
            mock_imap_cls.return_value = mock_imap

            mock_httpx.Client.side_effect = OSError("Connection refused")

            handler._handle_push_to_file_hub("<fherr@example.com>")

        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 502

    def test_file_hub_http_error(self, single_db: str) -> None:
        _populate_db_with_attachments(
            single_db,
            [
                {
                    "message_id": "<fhhttp@example.com>",
                    "sender": "x@x.com",
                    "subject": "Has attachments",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                    "attachments_json": _ATTACHMENTS_JSON,
                    "imap_uid": "42",
                    "source_folder": "INBOX",
                },
            ],
        )
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        accounts = _make_accounts()
        handler = _AttachmentFakeHandler(
            single_db, mail_config=mail_config, accounts=accounts
        )
        handler.headers.get.return_value = 0

        raw_email = _make_mime_message(
            [("report.pdf", "application/pdf", b"%PDF-1.4 fake")]
        )

        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_imap_cls,
            mock.patch(
                "robotsix_auto_mail.server._attachment_mixin.httpx"
            ) as mock_httpx,
        ):
            mock_imap = mock.MagicMock()
            mock_imap.__enter__ = mock.MagicMock(return_value=mock_imap)
            mock_imap.__exit__ = mock.MagicMock(return_value=False)
            mock_imap.fetch_messages.return_value = [(42, raw_email)]
            mock_imap_cls.return_value = mock_imap

            mock_httpx_client = mock.MagicMock()
            mock_httpx_client.__enter__ = mock.MagicMock(
                return_value=mock_httpx_client
            )
            mock_httpx_client.__exit__ = mock.MagicMock(return_value=False)
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 413
            mock_resp.text = "Content Too Large"
            mock_httpx_client.post.return_value = mock_resp
            mock_httpx.Client.return_value = mock_httpx_client

            handler._handle_push_to_file_hub("<fhhttp@example.com>")

        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 502
