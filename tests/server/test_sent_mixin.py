"""Unit tests for ``_SentMixin`` — read-only Sent-folder chat API."""

from __future__ import annotations

from email.message import EmailMessage
from unittest import mock

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.server._sent_mixin import _SentMixin
from robotsix_auto_mail.server._view_mixin import _BoardViewMixin


class _FakeHandler(_BoardViewMixin, _SentMixin):
    """Concrete ``_SentMixin`` with protocol attributes wired to mocks."""

    def __init__(
        self,
        mail_config: MailConfig | None = None,
        *,
        path: str = "/sent/messages",
        _aggregate: bool = False,
    ) -> None:
        self.db_path = "test.db"
        self.mail_config = mail_config
        self.path = path
        self._aggregate = _aggregate
        self.accounts = None
        self._current_account_id = None
        self._account_cookie = None
        self._send_response = mock.MagicMock()
        self._not_found = mock.MagicMock()
        self._bad_request = mock.MagicMock()
        self._serve_json = mock.MagicMock()


def _mock_client(**attrs: object) -> mock.MagicMock:
    from robotsix_auto_mail.imap.client import ImapClient

    client = mock.MagicMock(spec=ImapClient)
    client.__enter__ = mock.MagicMock(return_value=client)
    client.__exit__ = mock.MagicMock(return_value=None)
    for key, value in attrs.items():
        setattr(client, key, value)
    return client


def _sent_folder() -> mock.MagicMock:
    folder = mock.MagicMock()
    folder.name = "Sent"
    folder.delimiter = "/"
    folder.attributes = ("\\Sent",)
    return folder


class TestServeSentMessages:
    def test_aggregate_short_circuits(self) -> None:
        handler = _FakeHandler(_aggregate=True)
        handler._serve_sent_messages()
        handler._serve_json.assert_called_once_with({"messages": [], "folder": ""})

    def test_no_mail_config_returns_503(self) -> None:
        handler = _FakeHandler(mail_config=None)
        handler._serve_sent_messages()
        handler._serve_json.assert_called_once_with(
            {"error": "IMAP not configured for this account"},
            status=503,
        )

    def test_lists_sent_messages(self, cfg: MailConfig) -> None:
        client = _mock_client(
            list_folders=mock.MagicMock(return_value=[_sent_folder()]),
            search_uids=mock.MagicMock(return_value=[1, 2, 3]),
            fetch_envelopes=mock.MagicMock(
                return_value=[
                    {
                        "uid": 3,
                        "subject": "Invoice",
                        "from": "me@example.com",
                        "to": "client@tii.ae",
                        "date": "2024-01-01",
                        "size": 100,
                        "flags": ["\\Seen"],
                        "message_id": "<invoice@example.com>",
                    }
                ]
            ),
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler = _FakeHandler(mail_config=cfg)
            handler._serve_sent_messages()

        client.select_folder.assert_called_once_with("Sent")
        payload = handler._serve_json.call_args[0][0]
        assert payload["folder"] == "Sent"
        assert payload["total"] == 3
        assert payload["messages"][0]["to"] == "client@tii.ae"
        # Newest-first ordering: reversed UIDs passed to fetch_envelopes.
        assert client.fetch_envelopes.call_args[0][0] == [3, 2, 1]

    def test_offset_and_limit(self, cfg: MailConfig) -> None:
        client = _mock_client(
            list_folders=mock.MagicMock(return_value=[_sent_folder()]),
            search_uids=mock.MagicMock(return_value=[1, 2, 3, 4, 5]),
            fetch_envelopes=mock.MagicMock(return_value=[]),
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler = _FakeHandler(
                mail_config=cfg,
                path="/sent/messages?limit=2&offset=1",
            )
            handler._serve_sent_messages()

        # reversed = [5,4,3,2,1]; offset 1, limit 2 → [4, 3]
        assert client.fetch_envelopes.call_args[0][0] == [4, 3]

    def test_no_sent_folder_returns_404(self, cfg: MailConfig) -> None:
        other = mock.MagicMock()
        other.name = "INBOX"
        other.delimiter = "/"
        other.attributes = ()
        client = _mock_client(
            list_folders=mock.MagicMock(return_value=[other]),
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler = _FakeHandler(mail_config=cfg)
            handler._serve_sent_messages()

        handler._not_found.assert_called_once()

    def test_imap_error_returns_502(self, cfg: MailConfig) -> None:
        from robotsix_auto_mail.imap.errors import ImapError

        client = _mock_client(
            list_folders=mock.MagicMock(side_effect=ImapError("boom")),
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler = _FakeHandler(mail_config=cfg)
            handler._serve_sent_messages()

        assert handler._send_response.call_args[1]["status"] == 502


class TestServeSentMessage:
    def _raw_message(self) -> bytes:
        msg = EmailMessage()
        msg["Message-ID"] = "<abc@example.com>"
        msg["From"] = "me@example.com"
        msg["To"] = "client@tii.ae"
        msg["Subject"] = "Invoice"
        msg.set_content("Please find attached.")
        msg.add_attachment(
            b"PDFDATA",
            maintype="application",
            subtype="pdf",
            filename="invoice.pdf",
        )
        return msg.as_bytes()

    def test_missing_uid_returns_400(self, cfg: MailConfig) -> None:
        handler = _FakeHandler(mail_config=cfg, path="/sent/message")
        handler._serve_sent_message()
        handler._bad_request.assert_called_once()

    def test_non_integer_uid_returns_400(self, cfg: MailConfig) -> None:
        handler = _FakeHandler(mail_config=cfg, path="/sent/message?uid=abc")
        handler._serve_sent_message()
        handler._bad_request.assert_called_once()

    def test_aggregate_returns_404(self, cfg: MailConfig) -> None:
        handler = _FakeHandler(
            mail_config=cfg, path="/sent/message?uid=1", _aggregate=True
        )
        handler._serve_sent_message()
        handler._not_found.assert_called_once()

    def test_reads_message_and_enumerates_attachments(self, cfg: MailConfig) -> None:
        client = _mock_client(
            list_folders=mock.MagicMock(return_value=[_sent_folder()]),
            fetch_messages=mock.MagicMock(return_value=[(7, self._raw_message())]),
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler = _FakeHandler(mail_config=cfg, path="/sent/message?uid=7")
            handler._serve_sent_message()

        client.select_folder.assert_called_once_with("Sent")
        payload = handler._serve_json.call_args[0][0]
        assert payload["uid"] == 7
        assert payload["folder"] == "Sent"
        assert payload["subject"] == "Invoice"
        assert payload["to"] == ["client@tii.ae"]
        assert "Please find attached." in payload["body_plain"]
        assert payload["attachments"][0]["filename"] == "invoice.pdf"

    def test_uid_not_found_returns_404(self, cfg: MailConfig) -> None:
        client = _mock_client(
            list_folders=mock.MagicMock(return_value=[_sent_folder()]),
            fetch_messages=mock.MagicMock(return_value=[]),
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler = _FakeHandler(mail_config=cfg, path="/sent/message?uid=99")
            handler._serve_sent_message()

        handler._not_found.assert_called_once()

    def test_no_mail_config_returns_503(self) -> None:
        handler = _FakeHandler(mail_config=None, path="/sent/message?uid=1")
        handler._serve_sent_message()
        handler._serve_json.assert_called_once_with(
            {"error": "IMAP not configured for this account"},
            status=503,
        )
