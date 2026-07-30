"""Body extraction and body overwrite protection tests for the MIME parser (_parse.py)."""

from __future__ import annotations

import email.mime.application
import email.mime.message
import email.mime.multipart
import email.mime.text
import json

from robotsix_auto_mail.pipeline._parse import parse_message

# ---------------------------------------------------------------------------
# Body extraction
# ---------------------------------------------------------------------------


def test_single_part_text_plain() -> None:
    raw = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: Hi\r\n"
        b"Date: Wed, 15 Jan 2025 10:30:00 +0000\r\n"
        b"Message-ID: <x@y>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"hello world"
    )
    record = parse_message(raw)
    assert record.body_plain == "hello world"
    assert record.body_html == ""


def test_single_part_text_html() -> None:
    raw = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: Hi\r\n"
        b"Date: Wed, 15 Jan 2025 10:30:00 +0000\r\n"
        b"Message-ID: <x@y>\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<p>hello</p>"
    )
    record = parse_message(raw)
    assert record.body_plain == ""
    assert record.body_html == "<p>hello</p>"


def test_multipart_alternative() -> None:
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg.attach(email.mime.text.MIMEText("plain text", "plain"))
    msg.attach(email.mime.text.MIMEText("<b>html</b>", "html"))
    msg["Subject"] = "Alt"
    msg["From"] = "a@x.com"
    msg["Date"] = "Wed, 15 Jan 2025 10:30:00 +0000"
    record = parse_message(msg.as_bytes())
    assert record.body_plain == "plain text"
    assert record.body_html == "<b>html</b>"


def test_plain_text_as_attachment_not_body() -> None:
    """A text part with Content-Disposition: attachment is an attachment, not body."""
    msg = email.mime.multipart.MIMEMultipart("mixed")
    msg.attach(email.mime.text.MIMEText("real body", "plain"))
    text_att = email.mime.text.MIMEText("attachment text", "plain")
    text_att.add_header("Content-Disposition", "attachment", filename="note.txt")
    msg.attach(text_att)
    msg["Subject"] = "Att"
    msg["From"] = "a@x.com"
    msg["Date"] = "Wed, 15 Jan 2025 10:30:00 +0000"
    record = parse_message(msg.as_bytes())
    assert record.body_plain == "real body"
    atts = json.loads(record.attachments_json)
    assert len(atts) == 1
    assert atts[0]["filename"] == "note.txt"


def test_nested_multipart_mixed_with_alternative() -> None:
    """multipart/mixed containing multipart/alternative + attachment."""
    mixed = email.mime.multipart.MIMEMultipart("mixed")
    alt = email.mime.multipart.MIMEMultipart("alternative")
    alt.attach(email.mime.text.MIMEText("plain text", "plain"))
    alt.attach(email.mime.text.MIMEText("<b>html</b>", "html"))
    mixed.attach(alt)
    att = email.mime.application.MIMEApplication(b"pdf bytes", "pdf")
    att.add_header("Content-Disposition", "attachment", filename="report.pdf")
    mixed.attach(att)
    mixed["Subject"] = "Nested"
    mixed["From"] = "a@x.com"
    mixed["Date"] = "Wed, 15 Jan 2025 10:30:00 +0000"
    record = parse_message(mixed.as_bytes())
    assert record.body_plain == "plain text"
    assert record.body_html == "<b>html</b>"
    atts = json.loads(record.attachments_json)
    assert len(atts) == 1
    assert atts[0]["filename"] == "report.pdf"


# ---------------------------------------------------------------------------
# Body overwrite protection (depth-first walk)
# ---------------------------------------------------------------------------


def test_forwarded_message_outer_body_preserved() -> None:
    """Outer text/plain is non-empty; inner forwarded text/plain is empty."""
    outer = email.mime.multipart.MIMEMultipart("mixed")
    outer.attach(email.mime.text.MIMEText("Real outer body", "plain"))

    inner_msg = email.mime.text.MIMEText("", "plain")
    inner_msg["Subject"] = "Fwd"
    inner_msg["From"] = "b@x.com"
    inner_msg["Date"] = "Wed, 15 Jan 2025 10:30:00 +0000"
    inner = email.mime.message.MIMEMessage(inner_msg)
    outer.attach(inner)

    outer["Subject"] = "Fwd test"
    outer["From"] = "a@x.com"
    outer["Date"] = "Wed, 15 Jan 2025 10:30:00 +0000"
    record = parse_message(outer.as_bytes())
    assert record.body_plain == "Real outer body"


def test_multi_level_text_plain_first_non_empty_wins() -> None:
    """Two text/plain parts at different levels: first non-empty is preserved."""
    outer = email.mime.multipart.MIMEMultipart("mixed")
    outer.attach(email.mime.text.MIMEText("First", "plain"))

    nested_mixed = email.mime.multipart.MIMEMultipart("mixed")
    nested_mixed.attach(email.mime.text.MIMEText("", "plain"))
    outer.attach(nested_mixed)

    outer["Subject"] = "Multi"
    outer["From"] = "a@x.com"
    outer["Date"] = "Wed, 15 Jan 2025 10:30:00 +0000"
    record = parse_message(outer.as_bytes())
    assert record.body_plain == "First"


def test_alternative_empty_plain_html_preserved() -> None:
    """multipart/alternative with empty text/plain and non-empty text/html."""
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg.attach(email.mime.text.MIMEText("", "plain"))
    msg.attach(email.mime.text.MIMEText("<b>html content</b>", "html"))
    msg["Subject"] = "Alt empty plain"
    msg["From"] = "a@x.com"
    msg["Date"] = "Wed, 15 Jan 2025 10:30:00 +0000"
    record = parse_message(msg.as_bytes())
    assert record.body_plain == ""
    assert record.body_html == "<b>html content</b>"


def test_outer_empty_inner_non_empty_fallback() -> None:
    """Outer text/plain is empty, inner text/plain has content — fallback."""
    outer = email.mime.multipart.MIMEMultipart("mixed")
    outer.attach(email.mime.text.MIMEText("", "plain"))

    nested_mixed = email.mime.multipart.MIMEMultipart("mixed")
    nested_mixed.attach(email.mime.text.MIMEText("Inner body", "plain"))
    outer.attach(nested_mixed)

    outer["Subject"] = "Fallback"
    outer["From"] = "a@x.com"
    outer["Date"] = "Wed, 15 Jan 2025 10:30:00 +0000"
    record = parse_message(outer.as_bytes())
    assert record.body_plain == "Inner body"
