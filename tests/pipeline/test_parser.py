"""Metadata, edge-case, and error-handling tests for the MIME parser (_parse.py)."""

from __future__ import annotations

import email.mime.application
import email.mime.multipart
import email.mime.text
import json

from robotsix_auto_mail.pipeline._parse import ParseError, parse_message

# ---------------------------------------------------------------------------
# Attachment metadata
# ---------------------------------------------------------------------------


def test_attachment_metadata() -> None:
    msg = email.mime.multipart.MIMEMultipart("mixed")
    msg.attach(email.mime.text.MIMEText("body", "plain"))
    att = email.mime.application.MIMEApplication(b"x" * 100, "pdf")
    att.add_header("Content-Disposition", "attachment", filename="report.pdf")
    msg.attach(att)
    msg["Subject"] = "Att"
    msg["From"] = "a@x.com"
    msg["Date"] = "Wed, 15 Jan 2025 10:30:00 +0000"
    record = parse_message(msg.as_bytes())
    atts = json.loads(record.attachments_json)
    assert len(atts) == 1
    assert atts[0]["filename"] == "report.pdf"
    assert atts[0]["mime_type"] == "application/pdf"
    assert atts[0]["size"] == 100


def test_attachment_no_filename() -> None:
    msg = email.mime.multipart.MIMEMultipart("mixed")
    msg.attach(email.mime.text.MIMEText("body", "plain"))
    att = email.mime.application.MIMEApplication(b"data", "octet-stream")
    msg.attach(att)
    msg["Subject"] = "NoFilename"
    msg["From"] = "a@x.com"
    msg["Date"] = "Wed, 15 Jan 2025 10:30:00 +0000"
    record = parse_message(msg.as_bytes())
    atts = json.loads(record.attachments_json)
    assert atts[0]["filename"] == ""
    assert atts[0]["mime_type"] == "application/octet-stream"


def test_multiple_attachments() -> None:
    """multipart/mixed with a plain-text body and two attachments."""
    msg = email.mime.multipart.MIMEMultipart("mixed")
    msg.attach(email.mime.text.MIMEText("body text here", "plain"))

    att1 = email.mime.application.MIMEApplication(b"a" * 200, "pdf")
    att1.add_header("Content-Disposition", "attachment", filename="report.pdf")
    msg.attach(att1)

    att2 = email.mime.application.MIMEApplication(b"b" * 300, "png")
    att2.add_header("Content-Disposition", "attachment", filename="image.png")
    msg.attach(att2)

    msg["Subject"] = "MultiAtt"
    msg["From"] = "a@x.com"
    msg["Date"] = "Wed, 15 Jan 2025 10:30:00 +0000"

    record = parse_message(msg.as_bytes())
    atts = json.loads(record.attachments_json)

    assert len(atts) == 2
    assert record.body_plain == "body text here"

    assert atts[0]["filename"] == "report.pdf"
    assert atts[0]["mime_type"] == "application/pdf"
    assert atts[0]["size"] == 200

    assert atts[1]["filename"] == "image.png"
    assert atts[1]["mime_type"] == "application/png"
    assert atts[1]["size"] == 300


# ---------------------------------------------------------------------------
# imap_uid
# ---------------------------------------------------------------------------


def test_imap_uid_passed_through() -> None:
    raw = (
        b"From: alice@example.com\r\n"
        b"Subject: Hi\r\n"
        b"Date: Wed, 15 Jan 2025 10:30:00 +0000\r\n"
        b"Message-ID: <x@y>\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body"
    )
    record = parse_message(raw, imap_uid=42)
    assert record.imap_uid == 42


def test_imap_uid_defaults_to_none() -> None:
    raw = (
        b"From: alice@example.com\r\n"
        b"Subject: Hi\r\n"
        b"Date: Wed, 15 Jan 2025 10:30:00 +0000\r\n"
        b"Message-ID: <x@y>\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body"
    )
    record = parse_message(raw)
    assert record.imap_uid is None


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


def test_empty_body_no_text_parts() -> None:
    """Message with no text parts at all."""
    msg = email.mime.multipart.MIMEMultipart("mixed")
    att = email.mime.application.MIMEApplication(b"data", "octet-stream")
    att.add_header("Content-Disposition", "attachment", filename="file.bin")
    msg.attach(att)
    msg["Subject"] = "NoText"
    msg["From"] = "a@x.com"
    msg["Date"] = "Wed, 15 Jan 2025 10:30:00 +0000"
    record = parse_message(msg.as_bytes())
    assert record.body_plain == ""
    assert record.body_html == ""


def test_undeclarable_charset_fallback() -> None:
    """Non-existent charset falls back through the chain."""
    raw = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: Hi\r\n"
        b"Date: Wed, 15 Jan 2025 10:30:00 +0000\r\n"
        b"Message-ID: <x@y>\r\n"
        b"Content-Type: text/plain; charset=x-nonexistent-zzz\r\n"
        b"\r\n"
        b"hello"
    )
    record = parse_message(raw)
    assert record.body_plain == "hello"


# ---------------------------------------------------------------------------
# ParseError
# ---------------------------------------------------------------------------


def test_parse_error_is_exception() -> None:
    assert issubclass(ParseError, Exception)


def test_parse_error_docstring() -> None:
    assert ParseError.__doc__ is not None
    assert "MIME" in ParseError.__doc__ or "mime" in ParseError.__doc__
