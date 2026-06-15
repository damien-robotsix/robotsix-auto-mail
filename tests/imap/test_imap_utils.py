"""Unit tests for IMAP encoding utilities (robotsix_auto_mail.imap.utils)."""

from __future__ import annotations

import pytest

from robotsix_auto_mail.imap.utils import (
    _encode_mailbox,
    _is_gmail_host,
    _modified_b64decode,
    _modified_b64encode,
    imap_utf7_decode,
    imap_utf7_encode,
)

# =========================================================================
# _is_gmail_host
# =========================================================================


@pytest.mark.parametrize(
    "host",
    [
        "imap.gmail.com",
        "imap.googlemail.com",
        "IMAP.GMAIL.COM",
        "Imap.GoogleMail.Com",
        "  imap.gmail.com  ",
        "imap.gmail.com.",
        "imap.gmail.com..",
    ],
)
def test_is_gmail_host_true(host: str) -> None:
    """Recognises Gmail / Google Workspace hosts (case/whitespace/dot tolerant)."""
    assert _is_gmail_host(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "outlook.office365.com",
        "imap.mail.yahoo.com",
        "mail.example.com",
        "gmail.com.evil.com",
        "",
    ],
)
def test_is_gmail_host_false(host: str) -> None:
    """Correctly rejects non-Gmail hosts."""
    assert _is_gmail_host(host) is False


# =========================================================================
# _modified_b64encode / _modified_b64decode
# =========================================================================


@pytest.mark.parametrize(
    "text",
    [
        "Hello",
        "Préfecture",
        "Ångström",
        "日本語",
        "🧪",
        "",
        "a" * 100,
    ],
)
def test_modified_b64_roundtrip(text: str) -> None:
    """_modified_b64decode inverts _modified_b64encode for any text."""
    assert _modified_b64decode(_modified_b64encode(text)) == text


def test_modified_b64encode_uses_comma_not_slash() -> None:
    """RFC 3501 modified BASE64 uses ',' in place of '/'."""
    # \uffff encodes to //8= in standard BASE64 → ,,8 in modified BASE64
    encoded = _modified_b64encode("\uffff")
    assert "/" not in encoded
    assert "," in encoded


def test_modified_b64encode_no_padding() -> None:
    """RFC 3501 modified BASE64 strips trailing '=' padding."""
    encoded = _modified_b64encode("test")
    assert not encoded.endswith("=")


def test_modified_b64encode_known_value() -> None:
    """Smoke test: vérify a known modified-BASE64 encoding."""
    # "é" (U+00E9) → UTF-16BE: 0x00 0xE9 → base64: "AOk=" → modified: "AOk"
    assert _modified_b64encode("é") == "AOk"


# =========================================================================
# imap_utf7_encode
# =========================================================================


@pytest.mark.parametrize(
    ("decoded", "encoded"),
    [
        # Pure ASCII pass-through
        ("INBOX", "INBOX"),
        ("robotsix-mail-archive/Billing", "robotsix-mail-archive/Billing"),
        # Ampersand escaping
        ("&", "&-"),
        ("R&D", "R&-D"),
        ("a&b&c", "a&-b&-c"),
        # Non-ASCII characters → &BASE64- chunks
        ("Préfecture-44", "Pr&AOk-fecture-44"),
        ("Ångström", "&AMU-ngstr&APY-m"),
        # Empty string
        ("", ""),
        # Only non-ASCII
        ("日本語", "&ZeVnLIqe-"),
    ],
)
def test_imap_utf7_encode(decoded: str, encoded: str) -> None:
    """imap_utf7_encode produces the expected modified-UTF-7 output."""
    assert imap_utf7_encode(decoded) == encoded


def test_imap_utf7_encode_ascii_fast_path() -> None:
    """Pure ASCII (no ampersand, no non-ASCII) passes through verbatim."""
    assert imap_utf7_encode("Hello World 123!") == "Hello World 123!"


# =========================================================================
# imap_utf7_decode
# =========================================================================


@pytest.mark.parametrize(
    ("encoded", "decoded"),
    [
        # Fast path — no ampersand
        ("INBOX", "INBOX"),
        ("Hello World", "Hello World"),
        # Ampersand escape
        ("&-", "&"),
        ("R&-D", "R&D"),
        # Base64 chunks
        ("Pr&AOk-fecture-44", "Préfecture-44"),
        ("&AMU-ngstr&APY-m", "Ångström"),
        # Empty string
        ("", ""),
    ],
)
def test_imap_utf7_decode(encoded: str, decoded: str) -> None:
    """imap_utf7_decode correctly decodes modified-UTF-7 back to Unicode."""
    assert imap_utf7_decode(encoded) == decoded


def test_imap_utf7_decode_malformed_no_closing_dash() -> None:
    """Malformed input (no closing '-') passes the remainder verbatim."""
    # This preserves backwards-compatibility with unexpected server output.
    result = imap_utf7_decode("Pr&AOk-fecture-44&unclosed")
    assert result == "Préfecture-44&unclosed"


def test_imap_utf7_decode_malformed_only_ampersand() -> None:
    """A trailing '&' with no '-' passes through verbatim."""
    assert imap_utf7_decode("trailing&") == "trailing&"


def test_imap_utf7_decode_multiple_base64_chunks() -> None:
    """Two base64 chunks in one string decode correctly."""
    assert imap_utf7_decode("&AMU-ngstr&APY-m") == "Ångström"


# =========================================================================
# encode / decode round-trip property
# =========================================================================


@pytest.mark.parametrize(
    "name",
    [
        "INBOX",
        "Préfecture",
        "Ångström",
        "R&D",
        "[Gmail]/All Mail",
        "",
        "a&b&c",
        "日本語",
        "🧪 emoji test",
        "Mixed 中文 and English",
    ],
)
def test_imap_utf7_roundtrip(name: str) -> None:
    """decode(encode(s)) == s for a variety of mailbox names."""
    assert imap_utf7_decode(imap_utf7_encode(name)) == name


# =========================================================================
# _encode_mailbox
# =========================================================================


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # Safe atom → verbatim (no quoting needed)
        ("INBOX", "INBOX"),
        ("robotsix-mail-archive/Billing", "robotsix-mail-archive/Billing"),
        ("_Test.Folder-123", "_Test.Folder-123"),
        # Space-containing name → quoted
        ("[Gmail]/All Mail", '"[Gmail]/All Mail"'),
        ("Sent Mail", '"Sent Mail"'),
        # Non-ASCII → UTF-7 encoded + quoted
        ("Préfecture", '"Pr&AOk-fecture"'),
        ("Ångström", '"&AMU-ngstr&APY-m"'),
        # Ampersand in name
        ("R&D", '"R&-D"'),
        # Backslash escaping inside quoted string
        ("path\\to", '"path\\\\to"'),
        # Double-quote escaping inside quoted string
        ('folder"name', '"folder\\"name"'),
        # Both backslash and quote
        ('a\\"b', '"a\\\\\\"b"'),
    ],
)
def test_encode_mailbox(name: str, expected: str) -> None:
    """_encode_mailbox produces correctly quoted/encoded IMAP mailbox names."""
    assert _encode_mailbox(name) == expected


def test_encode_mailbox_empty_string() -> None:
    """Empty string is a safe atom (matches _ATOM_SAFE_RE → verbatim)."""
    # _ATOM_SAFE_RE.fullmatch("") on an empty string — re.fullmatch on ""
    # returns a match (zero-length match); the code checks `if encoded and ...`
    # so empty string is treated as falsy → quoted.
    assert _encode_mailbox("") == '""'


def test_encode_mailbox_spaces_always_quoted() -> None:
    """Any name containing a space is quoted regardless of other chars."""
    assert _encode_mailbox("A B") == '"A B"'
