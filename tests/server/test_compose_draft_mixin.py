"""Unit tests for ``_ComposeDraftMixin._handle_compose_draft``.

Covers: missing fields, unknown account, reply derivation, file-hub not
configured, file-hub unreachable, unknown file-hub id, fail-loud on a
failed attachment download, and the happy paths (new message + reply)
which write directly to the IMAP Drafts folder and store **no** board
record.
"""

from __future__ import annotations

import json
from unittest import mock

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.db import MailRecord, init_db, insert_record
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


def _mock_httpx_responses(*responses: object) -> mock._patch:
    """Patch the module ``httpx`` so ``Client().get`` yields *responses*."""
    patcher = mock.patch("robotsix_auto_mail.server._compose_draft_mixin.httpx")
    mock_httpx = patcher.start()
    mock_client = mock.MagicMock()
    mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
    mock_client.__exit__ = mock.MagicMock(return_value=False)
    mock_client.get.side_effect = list(responses)
    mock_httpx.Client.return_value = mock_client
    return patcher


def _resp(
    status_code: int, payload: dict | None = None, content: bytes = b"x"
) -> mock.MagicMock:
    r = mock.MagicMock()
    r.status_code = status_code
    if payload is not None:
        r.json.return_value = payload
    r.content = content
    return r


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

    def test_missing_body(self) -> None:
        handler = _ComposeDraftFakeHandler()
        _set_json_body(handler, {"account": "TEST", "to": "a@b.com", "subject": "S"})
        handler._handle_compose_draft()
        handler._bad_request.assert_called_once()
        assert "body" in handler._bad_request.call_args[0][0]

    def test_new_message_missing_to(self) -> None:
        handler = _ComposeDraftFakeHandler(accounts=_make_accounts())
        _set_json_body(handler, {"account": "TEST", "subject": "S", "body": "B"})
        handler._handle_compose_draft()
        handler._bad_request.assert_called_once()
        assert "to" in handler._bad_request.call_args[0][0]

    def test_new_message_missing_subject(self) -> None:
        handler = _ComposeDraftFakeHandler(accounts=_make_accounts())
        _set_json_body(handler, {"account": "TEST", "to": "a@b.com", "body": "B"})
        handler._handle_compose_draft()
        handler._bad_request.assert_called_once()
        assert "subject" in handler._bad_request.call_args[0][0]

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
        handler = _ComposeDraftFakeHandler(accounts=_make_accounts())
        _set_json_body(
            handler,
            {"account": "NONEXISTENT", "to": "a@b.com", "subject": "S", "body": "B"},
        )
        handler._handle_compose_draft()
        handler._send_response.assert_called_once()
        assert handler._send_response.call_args[1]["status"] == 404

    def test_no_accounts_configured(self) -> None:
        handler = _ComposeDraftFakeHandler(accounts=None)
        _set_json_body(
            handler,
            {"account": "TEST", "to": "a@b.com", "subject": "S", "body": "B"},
        )
        handler._handle_compose_draft()
        handler._bad_request.assert_called_once()
        assert "No accounts" in handler._bad_request.call_args[0][0]


class TestComposeDraftFileHubNotConfigured:
    def test_empty_file_hub_url_with_attachments(self) -> None:
        handler = _ComposeDraftFakeHandler(accounts=_make_accounts(file_hub_url=""))
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
        assert handler._send_response.call_args[1]["status"] == 503


class TestComposeDraftFileHubErrors:
    def _handler_with_attachment(self) -> _ComposeDraftFakeHandler:
        handler = _ComposeDraftFakeHandler(accounts=_make_accounts())
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
        return handler

    def test_file_hub_unreachable(self) -> None:
        handler = self._handler_with_attachment()
        patcher = mock.patch("robotsix_auto_mail.server._compose_draft_mixin.httpx")
        mock_httpx = patcher.start()
        try:
            mock_client = mock.MagicMock()
            mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
            mock_client.__exit__ = mock.MagicMock(return_value=False)
            mock_client.get.side_effect = RuntimeError("boom")
            mock_httpx.Client.return_value = mock_client
            handler._handle_compose_draft()
        finally:
            patcher.stop()
        handler._send_response.assert_called_once()
        assert handler._send_response.call_args[1]["status"] == 502
        assert (
            "unreachable" in json.loads(handler._send_response.call_args[0][0])["error"]
        )

    def test_file_hub_unknown_id(self) -> None:
        handler = self._handler_with_attachment()
        patcher = _mock_httpx_responses(_resp(404))
        try:
            handler._handle_compose_draft()
        finally:
            patcher.stop()
        handler._send_response.assert_called_once()
        assert handler._send_response.call_args[1]["status"] == 404
        assert "att-1" in json.loads(handler._send_response.call_args[0][0])["error"]

    def test_file_hub_server_error(self) -> None:
        handler = self._handler_with_attachment()
        patcher = _mock_httpx_responses(_resp(500))
        try:
            handler._handle_compose_draft()
        finally:
            patcher.stop()
        handler._send_response.assert_called_once()
        assert handler._send_response.call_args[1]["status"] == 502

    def test_attachment_download_fails_loudly(self) -> None:
        """Metadata OK but the content download 500s → fail loud, no append."""
        handler = self._handler_with_attachment()
        meta = _resp(200, {"filename": "f.pdf", "content_type": "application/pdf"})
        bad_content = _resp(500)
        patcher = _mock_httpx_responses(meta, bad_content)
        try:
            with mock.patch.object(handler, "_append_to_drafts_folder") as m_append:
                handler._handle_compose_draft()
        finally:
            patcher.stop()
        m_append.assert_not_called()
        handler._send_response.assert_called_once()
        assert handler._send_response.call_args[1]["status"] == 502


class TestComposeDraftHappyPath:
    def test_no_attachments_writes_no_board_record(self, single_db: str) -> None:
        handler = _ComposeDraftFakeHandler(accounts=_make_accounts(db_path=single_db))
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "to": "recipient@example.com",
                "subject": "Hello",
                "body": "Draft body text",
            },
        )
        with mock.patch.object(
            handler, "_append_to_drafts_folder", return_value="[Gmail]/Drafts"
        ) as m_append:
            handler._handle_compose_draft()

        handler._serve_json.assert_called_once()
        args = handler._serve_json.call_args
        assert args[1]["status"] == 201
        body = args[0][0]
        assert body["account"] == "TEST"
        assert body["to"] == "recipient@example.com"
        assert body["subject"] == "Hello"
        assert body["attachments"] == 0
        assert body["reply"] is False
        assert body["drafts_folder"] == "[Gmail]/Drafts"
        assert "message_id" not in body

        # The append received the composed message with no threading headers.
        kwargs = m_append.call_args[1]
        assert kwargs["from_addr"] == "user@example.com"
        assert kwargs["in_reply_to"] is None
        assert kwargs["cc"] is None

        # No board draft record is ever created.
        conn = init_db(single_db, skip_migrations=True)
        try:
            rows = conn.execute("SELECT COUNT(*) FROM mail_records").fetchone()
            assert rows[0] == 0
            decisions = conn.execute("SELECT COUNT(*) FROM triage_decisions").fetchone()
            assert decisions[0] == 0
        finally:
            conn.close()

    def test_with_attachments(self, single_db: str) -> None:
        handler = _ComposeDraftFakeHandler(accounts=_make_accounts(db_path=single_db))
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "to": "gestion@example.fr",
                "subject": "Mandat + RIB",
                "body": "Ci-joint le mandat et le RIB.",
                "attachments": ["mandate-pdf-id", "rib-pdf-id"],
            },
        )
        patcher = _mock_httpx_responses(
            _resp(200, {"filename": "mandate.pdf"}, b"PDF1"),
            _resp(200, content=b"PDF1"),
            _resp(200, {"filename": "rib.pdf"}, b"PDF2"),
            _resp(200, content=b"PDF2"),
        )
        try:
            with mock.patch.object(
                handler, "_append_to_drafts_folder", return_value="Drafts"
            ) as m_append:
                handler._handle_compose_draft()
        finally:
            patcher.stop()

        handler._serve_json.assert_called_once()
        body = handler._serve_json.call_args[0][0]
        assert body["attachments"] == 2
        kwargs = m_append.call_args[1]
        assert kwargs["attachment_names"] == ["mandate.pdf", "rib.pdf"]
        assert len(kwargs["attachment_files"]) == 2


class TestComposeDraftReply:
    def _insert_original(self, db_path: str) -> None:
        conn = init_db(db_path, skip_migrations=True)
        try:
            insert_record(
                conn,
                MailRecord(
                    message_id="<orig@example.com>",
                    sender="peer@example.com",
                    subject="Question about the invoice",
                    date="2026-01-01T00:00:00Z",
                    recipients_json=json.dumps(
                        {
                            "to": ["user@example.com", "team@example.com"],
                            "cc": ["cc@x.com"],
                        }
                    ),
                ),
            )
        finally:
            conn.close()

    def test_reply_derives_to_subject_and_threading(self, single_db: str) -> None:
        self._insert_original(single_db)
        handler = _ComposeDraftFakeHandler(accounts=_make_accounts(db_path=single_db))
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "body": "Here is my reply.",
                "reply_to_message_id": "<orig@example.com>",
            },
        )
        with mock.patch.object(
            handler, "_append_to_drafts_folder", return_value="Drafts"
        ) as m_append:
            handler._handle_compose_draft()

        handler._serve_json.assert_called_once()
        body = handler._serve_json.call_args[0][0]
        assert body["reply"] is True
        assert body["to"] == "peer@example.com"
        assert body["subject"] == "Re: Question about the invoice"
        kwargs = m_append.call_args[1]
        assert kwargs["in_reply_to"] == "<orig@example.com>"
        assert kwargs["references"] == "<orig@example.com>"
        assert kwargs["cc"] is None

    def test_reply_all_adds_cc(self, single_db: str) -> None:
        self._insert_original(single_db)
        handler = _ComposeDraftFakeHandler(accounts=_make_accounts(db_path=single_db))
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "body": "Reply to everyone.",
                "reply_to_message_id": "<orig@example.com>",
                "reply_all": True,
            },
        )
        with mock.patch.object(
            handler, "_append_to_drafts_folder", return_value="Drafts"
        ) as m_append:
            handler._handle_compose_draft()

        cc = m_append.call_args[1]["cc"]
        # self (user@) and the original sender (peer@) are excluded.
        assert "team@example.com" in cc
        assert "cc@x.com" in cc
        assert "user@example.com" not in cc
        assert "peer@example.com" not in cc

    def test_reply_unknown_target(self, single_db: str) -> None:
        handler = _ComposeDraftFakeHandler(accounts=_make_accounts(db_path=single_db))
        _set_json_body(
            handler,
            {
                "account": "TEST",
                "body": "Reply body.",
                "reply_to_message_id": "<missing@example.com>",
            },
        )
        handler._handle_compose_draft()
        handler._send_response.assert_called_once()
        assert handler._send_response.call_args[1]["status"] == 404
