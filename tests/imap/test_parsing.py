"""Direct unit tests for the inline IMAP FETCH parsing helpers.

``robotsix_auto_mail.imap._parsing`` is otherwise exercised only
indirectly through ``ImapClient`` fetch tests.  These tests drive each
parsing function on its own with valid, malformed, and edge-case input
(NIL values, quoted strings, parenthesised address lists, missing
fields) plus a Hypothesis fuzz that asserts the top-level parser never
raises on arbitrary bytes.
"""

from __future__ import annotations

import pytest

from robotsix_auto_mail.imap._parsing import (
    _format_first_address,
    _parse_envelope_inline,
    _parse_flags,
    _parse_inline_fetch_attrs,
)

# ---------------------------------------------------------------------------
# _parse_flags
# ---------------------------------------------------------------------------


def test_parse_flags_multiple() -> None:
    assert _parse_flags("\\Seen \\Answered") == ["\\Seen", "\\Answered"]


def test_parse_flags_empty() -> None:
    assert _parse_flags("") == []


def test_parse_flags_extra_whitespace() -> None:
    assert _parse_flags("   \\Seen    \\Flagged   ") == ["\\Seen", "\\Flagged"]


def test_parse_flags_single_keyword() -> None:
    assert _parse_flags("$Important") == ["$Important"]


# ---------------------------------------------------------------------------
# _format_first_address
# ---------------------------------------------------------------------------


def test_format_first_address_with_personal() -> None:
    assert (
        _format_first_address('("Alice" NIL "user" "example.com")')
        == "Alice <user@example.com>"
    )


def test_format_first_address_without_personal() -> None:
    assert _format_first_address('(NIL NIL "user" "example.com")') == "user@example.com"


def test_format_first_address_returns_first_of_many() -> None:
    text = '("Alice" NIL "a" "x.com")("Bob" NIL "b" "y.com")'
    assert _format_first_address(text) == "Alice <a@x.com>"


def test_format_first_address_empty() -> None:
    assert _format_first_address("") == ""
    assert _format_first_address("   ") == ""


def test_format_first_address_nil() -> None:
    assert _format_first_address("NIL") == ""


def test_format_first_address_too_few_parts() -> None:
    # Only three parts — cannot form an address.
    assert _format_first_address('("Alice" NIL "user")') == ""


def test_format_first_address_blank_mailbox_and_host() -> None:
    assert _format_first_address('(NIL NIL "" "")') == ""


# ---------------------------------------------------------------------------
# _parse_envelope_inline
# ---------------------------------------------------------------------------


def test_parse_envelope_inline_full() -> None:
    env = (
        '"01-Jan-2024 12:00:00 +0000" "Hello" '
        '(("Alice" NIL "user" "example.com")) '  # from
        "NIL NIL "  # sender, reply-to
        '(("Bob" NIL "bob" "example.com")) '  # to
        "NIL NIL NIL "  # cc, bcc, in-reply-to
        '"<id@example.com>"'  # message-id
    )
    result = _parse_envelope_inline(env)
    assert result["date"] == "01-Jan-2024 12:00:00 +0000"
    assert result["subject"] == "Hello"
    assert result["from"] == "Alice <user@example.com>"
    assert result["to"] == "Bob <bob@example.com>"
    assert result["message_id"] == "<id@example.com>"


def test_parse_envelope_inline_nil_fields() -> None:
    env = '"01-Jan-2024 12:00:00 +0000" NIL NIL NIL NIL NIL NIL NIL NIL "<id@x>"'
    result = _parse_envelope_inline(env)
    assert result["date"] == "01-Jan-2024 12:00:00 +0000"
    assert result["subject"] == ""
    assert result["from"] == ""
    assert result["to"] == ""
    assert result["message_id"] == "<id@x>"


def test_parse_envelope_inline_truncated() -> None:
    # Only date + subject present; the remaining fields default to empty.
    result = _parse_envelope_inline('"01-Jan-2024" "Subject only"')
    assert result["date"] == "01-Jan-2024"
    assert result["subject"] == "Subject only"
    assert result["from"] == ""
    assert result["to"] == ""
    assert result["message_id"] == ""


# ---------------------------------------------------------------------------
# _parse_inline_fetch_attrs
# ---------------------------------------------------------------------------


def _fetch_line() -> bytes:
    return (
        b'1 (FLAGS (\\Seen) INTERNALDATE "01-Jan-2024 12:00:00 +0000" '
        b"RFC822.SIZE 1234 ENVELOPE "
        b'("01-Jan-2024 12:00:00 +0000" "Hello" '
        b'(("Alice" NIL "user" "example.com")) '
        b'NIL NIL NIL NIL NIL NIL "<uid1@example.com>"))'
    )


def test_parse_inline_fetch_attrs_full() -> None:
    result = _parse_inline_fetch_attrs(_fetch_line())
    assert result is not None
    assert result["flags"] == ["\\Seen"]
    assert result["internal_date"] == "01-Jan-2024 12:00:00 +0000"
    assert result["size"] == 1234
    assert result["subject"] == "Hello"
    assert result["from"] == "Alice <user@example.com>"
    assert result["message_id"] == "<uid1@example.com>"


def test_parse_inline_fetch_attrs_no_paren_returns_none() -> None:
    assert _parse_inline_fetch_attrs(b"garbage with no parens") is None


def test_parse_inline_fetch_attrs_unterminated_returns_none() -> None:
    # Does not end with ')', so the guard rejects it.
    assert _parse_inline_fetch_attrs(b"1 (FLAGS (\\Seen) ") is None


def test_parse_inline_fetch_attrs_internaldate_fills_date() -> None:
    line = b'1 (INTERNALDATE "02-Feb-2025 08:30:00 +0000")'
    result = _parse_inline_fetch_attrs(line)
    assert result is not None
    assert result["internal_date"] == "02-Feb-2025 08:30:00 +0000"
    # date falls back to internal_date when the envelope provides none.
    assert result["date"] == "02-Feb-2025 08:30:00 +0000"


def test_parse_inline_fetch_attrs_invalid_size_stays_zero() -> None:
    result = _parse_inline_fetch_attrs(b"1 (RFC822.SIZE notanumber)")
    assert result is not None
    assert result["size"] == 0


def test_parse_inline_fetch_attrs_empty_flags() -> None:
    result = _parse_inline_fetch_attrs(b"1 (FLAGS ())")
    assert result is not None
    assert result["flags"] == []


# ---------------------------------------------------------------------------
# Property-based robustness
# ---------------------------------------------------------------------------

pytest.importorskip("hypothesis")

from hypothesis import given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402


@pytest.mark.slow
@given(st.binary(max_size=256))
def test_parse_inline_fetch_attrs_never_crashes(data: bytes) -> None:
    """Arbitrary bytes must yield a dict or ``None`` — never an exception."""
    result = _parse_inline_fetch_attrs(data)
    assert result is None or isinstance(result, dict)
