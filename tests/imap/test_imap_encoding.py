"""Tests for IMAP encoding utilities and special-use detection."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import (
    ImapClient,
    MailboxInfo,
    imap_utf7_decode,
    imap_utf7_encode,
    is_special_use,
)
from robotsix_auto_mail.imap.mailbox import _parse_list_line
from tests.conftest import _make_mock_imap_ssl

# ---------------------------------------------------------------------------
# Special-use detection (Gmail labels / RFC 6154)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attributes",
    [
        ("\\HasNoChildren", "\\All"),
        ("\\Sent",),
        ("\\Drafts",),
        ("\\Trash",),
        ("\\Junk",),
        ("\\Flagged",),
        ("\\Important",),
        ("\\HasChildren", "\\Noselect"),
        ("\\all",),  # case-insensitive
    ],
)
def test_is_special_use_true(attributes: tuple[str, ...]) -> None:
    """System / special-use mailboxes are recognised regardless of case."""
    assert is_special_use(MailboxInfo("x", attributes, "/")) is True


@pytest.mark.parametrize(
    "attributes",
    [
        (),
        ("\\HasNoChildren",),
        ("\\HasChildren",),
        ("\\Marked", "\\HasNoChildren"),
    ],
)
def test_is_special_use_false(attributes: tuple[str, ...]) -> None:
    """Ordinary (user) folders are not flagged as special-use."""
    assert is_special_use(MailboxInfo("Projects", attributes, "/")) is False


# ---------------------------------------------------------------------------
# Modified UTF-7 mailbox-name codec (RFC 3501)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("decoded", "encoded"),
    [
        ("INBOX", "INBOX"),  # pure ASCII → unchanged
        ("robotsix-mail-archive/Billing", "robotsix-mail-archive/Billing"),
        ("Préfecture-44", "Pr&AOk-fecture-44"),  # é → modified base64
        ("Administratif/Préfecture", "Administratif/Pr&AOk-fecture"),
        ("R&D", "R&-D"),  # literal ampersand → &-
        ("Ångström", "&AMU-ngstr&APY-m"),
    ],
)
def test_imap_utf7_roundtrip(decoded: str, encoded: str) -> None:
    """Encoding matches RFC 3501 and decode inverts encode."""
    assert imap_utf7_encode(decoded) == encoded
    assert imap_utf7_decode(encoded) == decoded


def test_parse_list_line_decodes_utf7_name() -> None:
    """A LIST response with a UTF-7 mailbox name is decoded to Unicode."""
    info = _parse_list_line(b'(\\HasNoChildren) "/" "Pr&AOk-fecture-44"')
    assert info.name == "Préfecture-44"


def test_select_quotes_names_with_spaces(cfg: MailConfig) -> None:
    """A mailbox name with a space (e.g. Gmail's All Mail) is sent quoted.

    stdlib imaplib does not quote mailbox names; an unquoted
    ``SELECT [Gmail]/All Mail`` is rejected ("Could not parse command").
    """
    mock_ssl = _make_mock_imap_ssl()
    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            client.select_folder("[Gmail]/All Mail")
            client.select_folder("INBOX")

    selected = [c.args[0] for c in mock_ssl.select.call_args_list]
    # Space-containing name is quoted; a bare atom is sent verbatim.
    assert '"[Gmail]/All Mail"' in selected
    assert "INBOX" in selected
