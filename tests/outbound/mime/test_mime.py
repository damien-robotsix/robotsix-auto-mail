"""Tests for the MIME message construction module."""

from __future__ import annotations

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from robotsix_auto_mail.outbound.mime import (
    build_multipart_message,
    build_plain_text_message,
)


def test_build_plain_text_message_basic() -> None:
    """build_plain_text_message constructs a MIMEText with standard headers."""
    msg = build_plain_text_message(
        from_addr="bot@example.com",
        to_addr="user@example.com",
        subject="Hello",
        body="Test body",
    )

    assert isinstance(msg, MIMEText)
    assert msg["From"] == "bot@example.com"
    assert msg["To"] == "user@example.com"
    assert msg["Subject"] == "Hello"
    assert "Date" in msg
    assert msg.get_content_type() == "text/plain"
    assert msg.get_content_charset() == "utf-8"


def test_build_plain_text_message_with_cc_and_threading() -> None:
    """build_plain_text_message sets Cc, In-Reply-To, and References headers."""
    msg = build_plain_text_message(
        from_addr="bot@example.com",
        to_addr="user@example.com",
        subject="Hello",
        body="Test body",
        cc=["a@x.com"],
        in_reply_to="<id>",
        references="<id>",
    )

    assert msg["Cc"] == "a@x.com"
    assert msg["In-Reply-To"] == "<id>"
    assert msg["References"] == "<id>"


def test_build_plain_text_message_utf8_body() -> None:
    """build_plain_text_message properly encodes non-ASCII bodies."""
    msg = build_plain_text_message(
        from_addr="bot@example.com",
        to_addr="user@example.com",
        subject="Café",
        body="résumé —  résumé",
    )

    decoded = msg.get_payload(decode=True)
    assert decoded is not None
    assert "résumé" in decoded.decode("utf-8")


def test_build_plain_text_message_date_header() -> None:
    """build_plain_text_message includes a Date header."""
    msg = build_plain_text_message(
        from_addr="a@b.com",
        to_addr="c@d.com",
        subject="S",
        body="B",
    )

    assert "Date" in msg
    assert msg["Date"] is not None


def test_build_plain_text_message_multiple_cc() -> None:
    """build_plain_text_message joins multiple Cc addresses with ', '."""
    msg = build_plain_text_message(
        from_addr="a@b.com",
        to_addr="c@d.com",
        subject="S",
        body="B",
        cc=["x@y.com", "z@w.com"],
    )

    assert msg["Cc"] == "x@y.com, z@w.com"


def test_build_plain_text_message_none_headers_omitted() -> None:
    """build_plain_text_message omits optional headers when None/empty."""
    msg = build_plain_text_message(
        from_addr="a@b.com",
        to_addr="c@d.com",
        subject="S",
        body="B",
    )

    assert "Cc" not in msg
    assert "In-Reply-To" not in msg
    assert "References" not in msg


def test_build_multipart_message_sets_common_headers() -> None:
    """build_multipart_message sets the same standard headers as the plain-text builder."""
    msg = build_multipart_message(
        from_addr="bot@example.com",
        to_addr="user@example.com",
        subject="Hello",
        body="Test body",
        attachments=[],
        attachment_names=[],
        cc=["a@x.com"],
        in_reply_to="<id>",
        references="<id>",
    )

    assert isinstance(msg, MIMEMultipart)
    assert msg["From"] == "bot@example.com"
    assert msg["To"] == "user@example.com"
    assert msg["Subject"] == "Hello"
    assert msg["Cc"] == "a@x.com"
    assert msg["In-Reply-To"] == "<id>"
    assert msg["References"] == "<id>"
    assert "Date" in msg


def test_build_multipart_message_omits_optional_headers() -> None:
    """build_multipart_message omits Cc/threading headers when unset."""
    msg = build_multipart_message(
        from_addr="a@b.com",
        to_addr="c@d.com",
        subject="S",
        body="B",
        attachments=[],
        attachment_names=[],
    )

    assert "Cc" not in msg
    assert "In-Reply-To" not in msg
    assert "References" not in msg


def test_build_multipart_message_attaches_text_part() -> None:
    """build_multipart_message attaches a UTF-8 text part and any attachments."""
    from io import BytesIO

    msg = build_multipart_message(
        from_addr="a@b.com",
        to_addr="c@d.com",
        subject="S",
        body="résumé",
        attachments=[BytesIO(b"payload")],
        attachment_names=["report.txt"],
    )

    assert msg.get_content_type() == "multipart/mixed"
    assert len(msg.get_payload()) == 2
    text_part = msg.get_payload()[0]
    assert isinstance(text_part, MIMEText)
    decoded = text_part.get_payload(decode=True)
    assert decoded is not None
    assert "résumé" in decoded.decode("utf-8")
    assert msg.get_payload()[1].get_filename() == "report.txt"
