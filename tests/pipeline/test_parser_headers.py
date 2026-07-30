"""Header extraction tests for the MIME parser (_parse.py)."""

from __future__ import annotations

import json

from robotsix_auto_mail.pipeline._parse import parse_message

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
