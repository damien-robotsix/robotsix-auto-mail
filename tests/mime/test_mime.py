"""Tests for the MIME message construction module."""

from __future__ import annotations

from email.mime.text import MIMEText

from robotsix_auto_mail.mime import build_plain_text_message


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
