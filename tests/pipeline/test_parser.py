"""Tests for the MIME parser (_parse.py)."""

from __future__ import annotations

import email.mime.application
import email.mime.base
import email.mime.message
import email.mime.multipart
import email.mime.text
import json

from robotsix_auto_mail.pipeline._parse import ParseError, parse_message

# ---------------------------------------------------------------------------
# Header extraction
# ---------------------------------------------------------------------------


def test_message_id_preserves_brackets() -> None:
    raw = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: Hi\r\n"
        b"Date: Wed, 15 Jan 2025 10:30:00 +0000\r\n"
        b"Message-ID: <abc123@example.com>\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body"
    )
    record = parse_message(raw)
    assert record.message_id == "<abc123@example.com>"


def test_message_id_missing_synthesizes_surrogate() -> None:
    raw = (
        b"From: alice@example.com\r\n"
        b"Subject: Hi\r\n"
        b"Date: Wed, 15 Jan 2025 10:30:00 +0000\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body"
    )
    record = parse_message(raw)
    # No Message-ID header → a stable surrogate is synthesized so the
    # record stays addressable (board actions and dedup key on message_id).
    assert record.message_id.startswith("<")
    assert record.message_id.endswith("@synthetic.robotsix-auto-mail>")
    # Deterministic: same bytes → same surrogate (dedup-safe)…
    assert parse_message(raw).message_id == record.message_id
    # …and different bytes → different surrogate.
    assert parse_message(raw + b"x").message_id != record.message_id


def test_sender_from_header() -> None:
    raw = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: Hi\r\n"
        b"Date: Wed, 15 Jan 2025 10:30:00 +0000\r\n"
        b"Message-ID: <x@y>\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body"
    )
    record = parse_message(raw)
    assert record.sender == "alice@example.com"


def test_sender_missing() -> None:
    raw = (
        b"To: bob@example.com\r\n"
        b"Subject: Hi\r\n"
        b"Date: Wed, 15 Jan 2025 10:30:00 +0000\r\n"
        b"Message-ID: <x@y>\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body"
    )
    record = parse_message(raw)
    assert record.sender == ""


def test_recipients_json_to_and_cc() -> None:
    raw = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com, carol@example.com\r\n"
        b"Cc: dave@example.com\r\n"
        b"Subject: Hi\r\n"
        b"Date: Wed, 15 Jan 2025 10:30:00 +0000\r\n"
        b"Message-ID: <x@y>\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body"
    )
    record = parse_message(raw)
    recipients = json.loads(record.recipients_json)
    assert recipients["to"] == ["bob@example.com", "carol@example.com"]
    assert recipients["cc"] == ["dave@example.com"]


def test_recipients_missing_headers() -> None:
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
    recipients = json.loads(record.recipients_json)
    assert recipients == {"to": [], "cc": []}


def test_subject_plain() -> None:
    raw = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: Hello World\r\n"
        b"Date: Wed, 15 Jan 2025 10:30:00 +0000\r\n"
        b"Message-ID: <x@y>\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body"
    )
    record = parse_message(raw)
    assert record.subject == "Hello World"


def test_subject_encoded_rfc2047() -> None:
    """RFC 2047 encoded-words are decoded by policy.default."""
    raw = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: =?utf-8?Q?Caf=C3=A9?=\r\n"
        b"Date: Wed, 15 Jan 2025 10:30:00 +0000\r\n"
        b"Message-ID: <x@y>\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body"
    )
    record = parse_message(raw)
    assert record.subject == "Café"


def test_subject_missing() -> None:
    raw = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Date: Wed, 15 Jan 2025 10:30:00 +0000\r\n"
        b"Message-ID: <x@y>\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body"
    )
    record = parse_message(raw)
    assert record.subject == ""


def test_date_iso8601() -> None:
    raw = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: Hi\r\n"
        b"Date: Wed, 15 Jan 2025 10:30:00 +0000\r\n"
        b"Message-ID: <x@y>\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body"
    )
    record = parse_message(raw)
    assert record.date == "2025-01-15T10:30:00+00:00"


def test_date_missing() -> None:
    raw = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: Hi\r\n"
        b"Message-ID: <x@y>\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body"
    )
    record = parse_message(raw)
    assert record.date == ""


def test_date_unparseable() -> None:
    raw = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: Hi\r\n"
        b"Date: not a valid date string at all\r\n"
        b"Message-ID: <x@y>\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body"
    )
    record = parse_message(raw)
    assert record.date == ""


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


# ---------------------------------------------------------------------------
# ParseError
# ---------------------------------------------------------------------------


def test_parse_error_is_exception() -> None:
    assert issubclass(ParseError, Exception)


def test_parse_error_docstring() -> None:
    assert ParseError.__doc__ is not None
    assert "MIME" in ParseError.__doc__ or "mime" in ParseError.__doc__
