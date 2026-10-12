"""Unit tests for ``ViewService.serve_board_card_body``.

Covers: aggregate-mode rejection, unknown card (404), missing IMAP
config / UID (502), IMAP fetch failure (502), the read-only happy path
(plain + sanitized HTML + attachment metadata), and the read-only
guarantee that the fetch goes through ``BODY.PEEK`` (``fetch_messages``)
and never calls a store/flag mutation.
"""

from __future__ import annotations

import email.message
from unittest import mock

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import init_db
from robotsix_auto_mail.imap import ImapError
from tests.server._view_mixin_helpers import _FakeHandler

pytest_plugins = ["tests.server._view_mixin_helpers"]


def _populate(db_path: str, **row: object) -> None:
    """Insert a single ``mail_records`` row honouring imap_uid/source_folder."""
    conn = init_db(db_path)
    try:
        conn.execute(
            "INSERT INTO mail_records "
            "(message_id, sender, subject, date, recipients_json, "
            "body_plain, body_html, attachments_json, status, "
            "imap_uid, source_folder) "
            "VALUES (?, ?, ?, ?, '{}', '', '', '[]', ?, ?, ?)",
            (
                row["message_id"],
                row.get("sender", "a@b.com"),
                row.get("subject", "Subject"),
                row.get("date", "2025-01-01T00:00:00"),
                row.get("status", "to_read"),
                row.get("imap_uid"),
                row.get("source_folder", "INBOX"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _make_raw_message() -> bytes:
    """Build a multipart message: plain + HTML (with a script) + attachment."""
    msg = email.message.EmailMessage()
    msg["Subject"] = "Qonto litige reply"
    msg["From"] = "support@qonto.com"
    msg["Message-ID"] = "<reply@qonto.com>"
    msg.set_content("Hello in plain text")
    msg.add_alternative(
        "<p>Hello in <b>HTML</b></p><script>alert(1)</script>",
        subtype="html",
    )
    msg.add_attachment(
        b"PDF-BYTES",
        maintype="application",
        subtype="pdf",
        filename="invoice.pdf",
    )
    return msg.as_bytes()


def _mail_config() -> MailConfig:
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="test",
        password="test",
    )


def _mock_imap(fetch_return: object) -> mock.MagicMock:
    """Build a context-manager ImapClient mock whose fetch returns *fetch_return*."""
    client = mock.MagicMock()
    client.__enter__ = mock.MagicMock(return_value=client)
    client.__exit__ = mock.MagicMock(return_value=False)
    client.fetch_messages.return_value = fetch_return
    return client


class TestServeBoardCardBody:
    def test_aggregate_mode_returns_400(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(
            tmp_db_path,
            _aggregate=True,
            path="/board-cards/x/body",
        )
        handler._serve_board_card_body()
        handler._serve_json.assert_called_once_with(
            {"error": "board-card body is per-account; use ?account=<id>"},
            status=400,
        )

    def test_unknown_card_returns_404(self, tmp_db_path: str) -> None:
        init_db(tmp_db_path).close()
        handler = _FakeHandler(
            tmp_db_path,
            mail_config=_mail_config(),
            path="/board-cards/%3Cmissing%40x.com%3E/body",
        )
        handler._serve_board_card_body()
        handler._not_found.assert_called_once()

    def test_no_imap_config_returns_502(self, tmp_db_path: str) -> None:
        _populate(tmp_db_path, message_id="<m@x.com>", imap_uid=7)
        handler = _FakeHandler(
            tmp_db_path,
            mail_config=None,
            path="/board-cards/%3Cm%40x.com%3E/body",
        )
        handler._serve_board_card_body()
        handler._problem.assert_called_once()
        assert handler._problem.call_args.kwargs["status"] == 502
        assert handler._problem.call_args.kwargs["kind"] == "imap-unavailable"

    def test_missing_uid_returns_502(self, tmp_db_path: str) -> None:
        _populate(tmp_db_path, message_id="<m@x.com>", imap_uid=None)
        handler = _FakeHandler(
            tmp_db_path,
            mail_config=_mail_config(),
            path="/board-cards/%3Cm%40x.com%3E/body",
        )
        handler._serve_board_card_body()
        handler._problem.assert_called_once()
        assert handler._problem.call_args.kwargs["kind"] == "imap-unavailable"

    def test_imap_fetch_error_returns_502(self, tmp_db_path: str) -> None:
        _populate(tmp_db_path, message_id="<m@x.com>", imap_uid=7)
        handler = _FakeHandler(
            tmp_db_path,
            mail_config=_mail_config(),
            path="/board-cards/%3Cm%40x.com%3E/body",
        )
        client = mock.MagicMock()
        client.__enter__ = mock.MagicMock(side_effect=ImapError("boom"))
        client.__exit__ = mock.MagicMock(return_value=False)
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler._serve_board_card_body()
        handler._problem.assert_called_once()
        assert handler._problem.call_args.kwargs["kind"] == "imap-fetch-failed"

    def test_message_gone_returns_502(self, tmp_db_path: str) -> None:
        _populate(tmp_db_path, message_id="<m@x.com>", imap_uid=7)
        handler = _FakeHandler(
            tmp_db_path,
            mail_config=_mail_config(),
            path="/board-cards/%3Cm%40x.com%3E/body",
        )
        with mock.patch(
            "robotsix_auto_mail.imap.ImapClient", return_value=_mock_imap([])
        ):
            handler._serve_board_card_body()
        handler._problem.assert_called_once()
        assert handler._problem.call_args.kwargs["kind"] == "message-unavailable"

    def test_happy_path_returns_body(self, tmp_db_path: str) -> None:
        _populate(
            tmp_db_path,
            message_id="<reply@qonto.com>",
            imap_uid=42,
            source_folder="INBOX",
        )
        handler = _FakeHandler(
            tmp_db_path,
            mail_config=_mail_config(),
            _current_account_id="gmailperso",
            path="/board-cards/%3Creply%40qonto.com%3E/body?account=gmailperso",
        )
        client = _mock_imap([(42, _make_raw_message())])
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler._serve_board_card_body()

        handler._serve_json.assert_called_once()
        payload = handler._serve_json.call_args[0][0]
        assert payload["message_id"] == "<reply@qonto.com>"
        assert payload["account"] == "gmailperso"
        assert "Hello in plain text" in payload["body_plain"]
        # HTML is present and sanitized: the <script> tag is stripped.
        assert payload["body_html"] is not None
        assert "Hello in" in payload["body_html"]
        assert "<script>" not in payload["body_html"].lower()
        assert payload["attachments"] == [
            {
                "filename": "invoice.pdf",
                "mime_type": "application/pdf",
                "size": len(b"PDF-BYTES"),
            }
        ]

    def test_read_only_uses_peek_fetch_only(self, tmp_db_path: str) -> None:
        """Read-only guarantee: only fetch_messages (BODY.PEEK) is used.

        No STORE/flag-mutating call is made on the IMAP client.
        """
        _populate(
            tmp_db_path,
            message_id="<reply@qonto.com>",
            imap_uid=42,
            source_folder="INBOX",
        )
        handler = _FakeHandler(
            tmp_db_path,
            mail_config=_mail_config(),
            path="/board-cards/%3Creply%40qonto.com%3E/body",
        )
        client = _mock_imap([(42, _make_raw_message())])
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler._serve_board_card_body()

        client.fetch_messages.assert_called_once_with([42])
        # No seen/flag mutation surface is touched.
        client.store.assert_not_called()
        client.mark_seen.assert_not_called()
        client.set_flags.assert_not_called()

    def test_plain_only_message_has_null_html(self, tmp_db_path: str) -> None:
        _populate(tmp_db_path, message_id="<p@x.com>", imap_uid=5)
        handler = _FakeHandler(
            tmp_db_path,
            mail_config=_mail_config(),
            path="/board-cards/%3Cp%40x.com%3E/body",
        )
        msg = email.message.EmailMessage()
        msg["Subject"] = "Plain only"
        msg["Message-ID"] = "<p@x.com>"
        msg.set_content("Just text, no HTML")
        client = _mock_imap([(5, msg.as_bytes())])
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler._serve_board_card_body()

        payload = handler._serve_json.call_args[0][0]
        assert "Just text, no HTML" in payload["body_plain"]
        assert payload["body_html"] is None
        assert payload["attachments"] == []
