"""Unit tests for ``_ComposeDraftMixin._handle_compose_draft``.

Covers: missing fields, unknown account, file-hub not configured,
file-hub unreachable, unknown file-hub id, happy path with and
without attachments.
"""

from __future__ import annotations

import json
from unittest import mock

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.server._compose_draft_mixin import _ComposeDraftMixin


class _ComposeDraftFakeHandler(_ComposeDraftMixin):
    """Concrete handler wiring ``BoardHandlerProtocol`` attributes to mocks."""

    def __init__(
        self,
        db_path: str = "",
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
        self._serve_json = mock.MagicMock()


def _make_config(db_path: str = "/tmp/test.db") -> MailConfig:  # noqa: S108 — test-only default
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        db_path=db_path,
    )


def _make_accounts(
    db_path: str = "/tmp/test.db",  # noqa: S108 — test-only default
    file_hub_url: str = "http://file-hub:8080",
) -> MailAccountsConfig:
    return MailAccountsConfig(
        accounts=[
            MailAccount(account_id="TEST", config=_make_config(db_path)),
        ],
        file_hub_url=file_hub_url,
    )


def _set_json_body(handler: _ComposeDraftFakeHandler, body: dict) -> None:
    """Set the handler's rfile to return the given JSON body."""
    raw = json.dumps(body).encode()
    handler.headers.get.return_value = len(raw)
    handler.rfile.read.return_value = raw


class TestComposeDraftMissingFields:
    def test_empty_body(self) -> None:
        handler = _ComposeDraftFakeHandler()
        handler.headers.get.return_value = 0
        handler.rfile.read.return_value = b""
        handler._handle_compose_draft()
        handler._bad_request.assert_called_once()
        assert "account" in handler._bad_request.call_args[0][0]

    def test_missing_account(self) -> None:
        handler = _ComposeDraftFakeHandler()
        _set_json_body(handler, {"to": "a@b.com", "subject": "S", "body": "B"})
        handler._handle_compose_draft()
        handler._bad_request.assert_called_once()
        assert "account" in handler._bad_request.call_args[0][0]

    def test_missing_to(self) -> None:
        handler = _ComposeDraftFakeHandler()
        _set_json_body(handler, {"account": "TEST", "subject": "S", "body": "B"})
        handler._handle_compose_draft()
        handler._bad_request.assert_called_once()
        assert "to" in handler._bad_request.call_args[0][0]

    def test_missing_subject(self) -> None:
        handler = _ComposeDraftFakeHandler()
        _set_json_body(handler, {"account": "TEST", "to": "a@b.com", "body": "B"})
        handler._handle_compose_draft()
        handler._bad_request.assert_called_once()
        assert "subject" in handler._bad_request.call_args[0][0]

    def test_missing_body(self) -> None:
        handler = _ComposeDraftFakeHandler()
        _set_json_body(handler, {"account": "TEST", "to": "a@b.com", "subject": "S"})
        handler._handle_compose_draft()
        handler._bad_request.assert_called_once()
        assert "body" in handler._bad_request.call_args[0][0]

    def test_malformed_json(self) -> None:
        handler = _ComposeDraftFakeHandler()
        handler.headers.get.return_value = 10
        handler.rfile.read.return_value = b"not json"
        handler._handle_compose_draft()
        handler._bad_request.assert_called_once()
        assert "Malformed" in handler._bad_request.call_args[0][0]

    def test_attachments_not_a_list(self) -> None:
        handler = _ComposeDraftFakeHandler()
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "to": "a@b.com",
                "subject": "S",
                "body": "B",
                "attachments": "not-a-list",
            },
        )
        handler._handle_compose_draft()
        handler._bad_request.assert_called_once()
        assert "attachments" in handler._bad_request.call_args[0][0]


class TestComposeDraftUnknownAccount:
    def test_unknown_account_id(self) -> None:
        accounts = _make_accounts()
        handler = _ComposeDraftFakeHandler(accounts=accounts)
        _set_json_body(
            handler,
            {
                "account": "NONEXISTENT",
                "to": "a@b.com",
                "subject": "S",
                "body": "B",
            },
        )
        handler._handle_compose_draft()
        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 404

    def test_no_accounts_configured(self) -> None:
        handler = _ComposeDraftFakeHandler(accounts=None)
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "to": "a@b.com",
                "subject": "S",
                "body": "B",
            },
        )
        handler._handle_compose_draft()
        handler._bad_request.assert_called_once()
        assert "No accounts" in handler._bad_request.call_args[0][0]


class TestComposeDraftFileHubNotConfigured:
    def test_empty_file_hub_url_with_attachments(self) -> None:
        accounts = _make_accounts(file_hub_url="")
        handler = _ComposeDraftFakeHandler(accounts=accounts)
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "to": "a@b.com",
                "subject": "S",
                "body": "B",
                "attachments": ["some-id"],
            },
        )
        handler._handle_compose_draft()
        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 503


class TestComposeDraftFileHubErrors:
    def test_file_hub_unreachable(self) -> None:
        accounts = _make_accounts()
        handler = _ComposeDraftFakeHandler(accounts=accounts)
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "to": "a@b.com",
                "subject": "S",
                "body": "B",
                "attachments": ["att-1"],
            },
        )

        with mock.patch(
            "robotsix_auto_mail.server._compose_draft_mixin.httpx"
        ) as mock_httpx:
            mock_client = mock.MagicMock()
            mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
            mock_client.__exit__ = mock.MagicMock(return_value=False)
            mock_client.get.side_effect = Exception("connection refused")
            mock_httpx.Client.return_value = mock_client

            handler._handle_compose_draft()

        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 502
        body = json.loads(args[0][0])
        assert "unreachable" in body["error"]

    def test_file_hub_unknown_id(self) -> None:
        accounts = _make_accounts()
        handler = _ComposeDraftFakeHandler(accounts=accounts)
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "to": "a@b.com",
                "subject": "S",
                "body": "B",
                "attachments": ["bad-id"],
            },
        )

        with mock.patch(
            "robotsix_auto_mail.server._compose_draft_mixin.httpx"
        ) as mock_httpx:
            mock_client = mock.MagicMock()
            mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
            mock_client.__exit__ = mock.MagicMock(return_value=False)
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 404
            mock_client.get.return_value = mock_resp
            mock_httpx.Client.return_value = mock_client

            handler._handle_compose_draft()

        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 404
        body = json.loads(args[0][0])
        assert "bad-id" in body["error"]

    def test_file_hub_server_error(self) -> None:
        accounts = _make_accounts()
        handler = _ComposeDraftFakeHandler(accounts=accounts)
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "to": "a@b.com",
                "subject": "S",
                "body": "B",
                "attachments": ["att-1"],
            },
        )

        with mock.patch(
            "robotsix_auto_mail.server._compose_draft_mixin.httpx"
        ) as mock_httpx:
            mock_client = mock.MagicMock()
            mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
            mock_client.__exit__ = mock.MagicMock(return_value=False)
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 500
            mock_resp.text = "Internal Server Error"
            mock_client.get.return_value = mock_resp
            mock_httpx.Client.return_value = mock_client

            handler._handle_compose_draft()

        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 502


class TestComposeDraftHappyPath:
    def test_no_attachments(self, single_db: str) -> None:
        accounts = _make_accounts(db_path=single_db)
        handler = _ComposeDraftFakeHandler(accounts=accounts)
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "to": "recipient@example.com",
                "subject": "Hello",
                "body": "Draft body text",
            },
        )

        handler._handle_compose_draft()

        handler._serve_json.assert_called_once()
        args = handler._serve_json.call_args
        assert args[1]["status"] == 201
        body = args[0][0]
        assert body["account"] == "TEST"
        assert body["to"] == "recipient@example.com"
        assert body["subject"] == "Hello"
        assert body["attachments"] == 0
        assert "message_id" in body

        # Verify the record was stored in the DB
        from robotsix_auto_mail.db import get_record_by_message_id, init_db

        conn = init_db(single_db, skip_migrations=True)
        try:
            record = get_record_by_message_id(conn, body["message_id"])
            assert record is not None
            assert record.sender == "user@example.com"
            assert record.subject == "Hello"
            assert record.draft_text == "Draft body text"
            recipients = json.loads(record.recipients_json)
            assert recipients["to"] == ["recipient@example.com"]
            assert json.loads(record.attachments_json) == []
        finally:
            conn.close()

    def test_with_attachments(self, single_db: str) -> None:
        accounts = _make_accounts(db_path=single_db)
        handler = _ComposeDraftFakeHandler(accounts=accounts)
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "to": "gestion.als@actionlogement.fr",
                "subject": "Mandat + RIB",
                "body": "Veuillez trouver ci-joint le mandat et le RIB.",
                "attachments": ["mandate-pdf-id", "rib-pdf-id"],
            },
        )

        file_hub_responses = [
            {
                "id": "mandate-pdf-id",
                "filename": "mandate_signe.pdf",
                "content_type": "application/pdf",
                "size": 12345,
            },
            {
                "id": "rib-pdf-id",
                "filename": "rib.pdf",
                "content_type": "application/pdf",
                "size": 6789,
            },
        ]

        with mock.patch(
            "robotsix_auto_mail.server._compose_draft_mixin.httpx"
        ) as mock_httpx:
            mock_client = mock.MagicMock()
            mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
            mock_client.__exit__ = mock.MagicMock(return_value=False)

            responses = []
            for resp_data in file_hub_responses:
                mock_resp = mock.MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = resp_data
                responses.append(mock_resp)

            mock_client.get.side_effect = responses
            mock_httpx.Client.return_value = mock_client

            handler._handle_compose_draft()

        handler._serve_json.assert_called_once()
        args = handler._serve_json.call_args
        assert args[1]["status"] == 201
        body = args[0][0]
        assert body["attachments"] == 2

        # Verify the record was stored with attachment metadata
        from robotsix_auto_mail.db import get_record_by_message_id, init_db

        conn = init_db(single_db, skip_migrations=True)
        try:
            record = get_record_by_message_id(conn, body["message_id"])
            assert record is not None
            att_meta = json.loads(record.attachments_json)
            assert len(att_meta) == 2
            assert att_meta[0]["file_hub_id"] == "mandate-pdf-id"
            assert att_meta[0]["filename"] == "mandate_signe.pdf"
            assert att_meta[0]["mime_type"] == "application/pdf"
            assert att_meta[0]["size"] == 12345
            assert att_meta[1]["file_hub_id"] == "rib-pdf-id"
            assert att_meta[1]["filename"] == "rib.pdf"

            # Verify triage decision is DRAFT_READY
            from robotsix_auto_mail.triage import get_triage_decision

            decision = get_triage_decision(conn, body["message_id"])
            assert decision is not None
            assert decision.action == "DRAFT_READY"
        finally:
            conn.close()

    def test_unique_message_ids(self, single_db: str) -> None:
        """Two compose-draft calls produce distinct message_ids."""
        accounts = _make_accounts(db_path=single_db)
        ids: list[str] = []
        for _ in range(2):
            handler = _ComposeDraftFakeHandler(accounts=accounts)
            _set_json_body(
                handler,
                {
                    "account": "TEST",
                    "to": "a@b.com",
                    "subject": "S",
                    "body": "B",
                },
            )
            handler._handle_compose_draft()
            args = handler._serve_json.call_args
            ids.append(args[0][0]["message_id"])
        assert ids[0] != ids[1]

    def test_with_pdf_attachment(self, single_db: str) -> None:
        """Regression: PDF attachment id stores metadata, not binary content.

        Previously, the handler fetched the raw file-download endpoint
        (/files/<id>) instead of the metadata endpoint (/files/<id>/metadata),
        then called resp.json() on binary PDF bytes, crashing with a
        UnicodeDecodeError.
        """
        accounts = _make_accounts(db_path=single_db)
        handler = _ComposeDraftFakeHandler(accounts=accounts)
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "to": "someone@example.com",
                "subject": "test",
                "body": "test",
                "attachments": ["44c50f3e-82b5-425f-893d-e945a1810b95"],
            },
        )

        pdf_meta = {
            "id": "44c50f3e-82b5-425f-893d-e945a1810b95",
            "filename": "report.pdf",
            "content_type": "application/pdf",
            "size": 98765,
        }

        with mock.patch(
            "robotsix_auto_mail.server._compose_draft_mixin.httpx"
        ) as mock_httpx:
            mock_client = mock.MagicMock()
            mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
            mock_client.__exit__ = mock.MagicMock(return_value=False)
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = pdf_meta
            mock_client.get.return_value = mock_resp
            mock_httpx.Client.return_value = mock_client

            handler._handle_compose_draft()

        handler._serve_json.assert_called_once()
        args = handler._serve_json.call_args
        assert args[1]["status"] == 201
        body = args[0][0]
        assert body["attachments"] == 1

        # Verify the URL was the metadata endpoint, not the download endpoint
        call_args = mock_client.get.call_args
        requested_url = call_args[0][0]
        assert "/metadata" in requested_url

        # Verify the attachment metadata was stored correctly
        from robotsix_auto_mail.db import get_record_by_message_id, init_db

        conn = init_db(single_db, skip_migrations=True)
        try:
            record = get_record_by_message_id(conn, body["message_id"])
            assert record is not None
            att_meta = json.loads(record.attachments_json)
            assert len(att_meta) == 1
            assert att_meta[0]["file_hub_id"] == "44c50f3e-82b5-425f-893d-e945a1810b95"
            assert att_meta[0]["filename"] == "report.pdf"
            assert att_meta[0]["mime_type"] == "application/pdf"
            assert att_meta[0]["size"] == 98765
        finally:
            conn.close()


class TestComposeDraftBinaryResponse:
    """Regression: handler returns 502 when file-hub returns binary content."""

    def test_binary_file_content_returns_502(self) -> None:
        """If file-hub returns binary (e.g. PDF bytes), the handler must
        return 502 instead of crashing with UnicodeDecodeError."""
        accounts = _make_accounts()
        handler = _ComposeDraftFakeHandler(accounts=accounts)
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "to": "a@b.com",
                "subject": "S",
                "body": "B",
                "attachments": ["44c50f3e-82b5-425f-893d-e945a1810b95"],
            },
        )

        # Simulate a response with binary PDF content (the original bug)
        pdf_binary = b"%PDF-1.4\n\xe2\xe3\xcf\xd3" + b"\x00" * 50

        with mock.patch(
            "robotsix_auto_mail.server._compose_draft_mixin.httpx"
        ) as mock_httpx:
            mock_client = mock.MagicMock()
            mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
            mock_client.__exit__ = mock.MagicMock(return_value=False)
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = pdf_binary
            # json() will raise UnicodeDecodeError on binary content
            mock_resp.json.side_effect = UnicodeDecodeError(
                "utf-8", b"\xe2", 0, 1, "invalid continuation byte"
            )
            mock_client.get.return_value = mock_resp
            mock_httpx.Client.return_value = mock_client

            handler._handle_compose_draft()

        handler._send_response.assert_called_once()
        args = handler._send_response.call_args
        assert args[1]["status"] == 502
        body = json.loads(args[0][0])
        assert "non-JSON" in body["error"]
