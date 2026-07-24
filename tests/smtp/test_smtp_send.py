"""Tests for SMTP client send()."""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.smtp import (
    SmtpClient,
    SmtpError,
    SmtpSendError,
)
from tests.conftest import _make_mock_smtp

# ===================================================================
# send() tests
# ===================================================================


def test_send_constructs_mime_and_calls_send_message(cfg: MailConfig) -> None:
    """send() builds a MIMEText with correct headers and calls
    send_message()."""
    mock_smtp = _make_mock_smtp()

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        client = SmtpClient(cfg)
        client.connect()
        client.send(
            from_addr="bot@example.com",
            to_addr="user@example.com",
            subject="Hello",
            body="Test body",
        )

    mock_smtp.send_message.assert_called_once()
    call_args, call_kwargs = mock_smtp.send_message.call_args

    msg = call_args[0]
    assert isinstance(msg, MIMEText)
    assert msg["From"] == "bot@example.com"
    assert msg["To"] == "user@example.com"
    assert msg["Subject"] == "Hello"
    assert "Date" in msg
    # MIMEText defaults to text/plain; charset utf-8
    assert msg.get_content_type() == "text/plain"
    assert msg.get_content_charset() == "utf-8"

    # Keyword arguments
    assert call_kwargs["from_addr"] == "bot@example.com"
    assert call_kwargs["to_addrs"] == ["user@example.com"]


def test_send_with_cc_and_threading_headers(cfg: MailConfig) -> None:
    """send() with cc/in_reply_to/references sets the headers and adds the
    Cc recipients to the SMTP envelope."""
    mock_smtp = _make_mock_smtp()

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        client = SmtpClient(cfg)
        client.connect()
        client.send(
            from_addr="bot@example.com",
            to_addr="user@example.com",
            subject="Hello",
            body="Test body",
            cc=["a@x.com"],
            in_reply_to="<id>",
            references="<id>",
        )

    mock_smtp.send_message.assert_called_once()
    call_args, call_kwargs = mock_smtp.send_message.call_args

    msg = call_args[0]
    assert msg["Cc"] == "a@x.com"
    assert msg["In-Reply-To"] == "<id>"
    assert msg["References"] == "<id>"

    # Envelope recipients must include both the To and Cc addresses.
    assert call_kwargs["to_addrs"] == ["user@example.com", "a@x.com"]


def test_send_body_is_utf8_encoded(cfg: MailConfig) -> None:
    """send() properly encodes non-ASCII bodies."""
    mock_smtp = _make_mock_smtp()

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        client = SmtpClient(cfg)
        client.connect()
        client.send(
            from_addr="bot@example.com",
            to_addr="user@example.com",
            subject="Café",
            body="résumé —  résumé",
        )

    msg = mock_smtp.send_message.call_args[0][0]
    # MIMEText base64-encodes non-ASCII bodies; verify the decoded payload.
    decoded = msg.get_payload(decode=True)
    assert decoded is not None
    assert "résumé" in decoded.decode("utf-8")


def test_send_includes_date_header(cfg: MailConfig) -> None:
    """send() includes a Date header via email.utils.formatdate()."""
    mock_smtp = _make_mock_smtp()

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        client = SmtpClient(cfg)
        client.connect()
        client.send(
            from_addr="a@b.com",
            to_addr="c@d.com",
            subject="S",
            body="B",
        )

    msg = mock_smtp.send_message.call_args[0][0]
    assert "Date" in msg
    assert msg["Date"] is not None


def test_send_failure_raises_smtp_send_error(cfg: MailConfig) -> None:
    """send() failure (SMTP rejection) → SmtpSendError."""
    mock_smtp = _make_mock_smtp()
    send_error = smtplib.SMTPException("Message rejected")
    mock_smtp.send_message.side_effect = send_error

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        client = SmtpClient(cfg)
        client.connect()
        with pytest.raises(SmtpSendError) as exc:
            client.send(
                from_addr="bot@example.com",
                to_addr="user@example.com",
                subject="Hello",
                body="World",
            )
        assert "Failed to send" in str(exc.value)
        assert exc.value.__cause__ is send_error


def test_send_before_connect_raises(cfg: MailConfig) -> None:
    """Calling send() before connect() raises SmtpError."""
    client = SmtpClient(cfg)
    with pytest.raises(SmtpError, match="Not connected"):
        client.send(
            from_addr="a@b.com",
            to_addr="c@d.com",
            subject="S",
            body="B",
        )
