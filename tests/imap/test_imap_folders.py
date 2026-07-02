"""Tests for IMAP folder operations: list, select, create, and waste-folder detection."""

from __future__ import annotations

import imaplib
from unittest import mock

import pytest
from tests.conftest import _make_mock_imap_ssl

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import (
    ImapClient,
    ImapError,
)
from robotsix_auto_mail.imap.mailbox import _is_waste_folder

# ---------------------------------------------------------------------------
# list_folders parsing
# ---------------------------------------------------------------------------


def test_list_folders_empty_delimiter(cfg: MailConfig) -> None:
    """LIST response with empty delimiter (flat namespace)."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.list.return_value = ("OK", [b'() "" "INBOX"'])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            folders = client.list_folders()

    assert folders[0].delimiter == ""


def test_list_folders_no_attributes(cfg: MailConfig) -> None:
    """LIST response with empty flags tuple."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.list.return_value = ("OK", [b'() "/" "Archive"'])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            folders = client.list_folders()

    assert folders[0].attributes == ()
    assert folders[0].name == "Archive"


def test_list_folders_multiple_flags(cfg: MailConfig) -> None:
    """LIST response with multiple flags including special ones."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.list.return_value = (
        "OK",
        [b'(\\Marked \\HasChildren) "/" "[Gmail]"'],
    )

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            folders = client.list_folders()

    assert folders[0].attributes == ("\\Marked", "\\HasChildren")


def test_list_folders_nil_delimiter(cfg: MailConfig) -> None:
    """LIST response with NIL delimiter."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.list.return_value = ("OK", [b'(\\HasNoChildren) NIL "INBOX"'])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            folders = client.list_folders()

    assert folders[0].delimiter == ""
    assert folders[0].name == "INBOX"


def test_list_folders_not_connected(cfg: MailConfig) -> None:
    """list_folders raises ImapError when the client is not connected."""
    client = ImapClient(cfg)
    with pytest.raises(ImapError, match="Not connected"):
        client.list_folders()


def test_list_folders_non_ok_status(cfg: MailConfig) -> None:
    """list_folders raises ImapError on non-OK LIST response."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.list.return_value = ("NO", [])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            with pytest.raises(ImapError, match="LIST command failed: NO"):
                client.list_folders()


# ---------------------------------------------------------------------------
# select_folder
# ---------------------------------------------------------------------------


def test_select_folder_returns_count(cfg: MailConfig) -> None:
    """select_folder parses the EXISTS count from the SELECT response."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.select.return_value = ("OK", [b"42"])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            count = client.select_folder("INBOX")

    mock_ssl.select.assert_called_once_with("INBOX")
    assert count == 42


def test_select_folder_no_count(cfg: MailConfig) -> None:
    """select_folder returns 0 when the server gives no count."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.select.return_value = ("OK", [None])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            count = client.select_folder("INBOX")

    assert count == 0


def test_select_folder_empty_data(cfg: MailConfig) -> None:
    """select_folder returns 0 when data list is empty."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.select.return_value = ("OK", [])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            count = client.select_folder("INBOX")

    assert count == 0


def test_select_folder_not_connected(cfg: MailConfig) -> None:
    """select_folder raises ImapError when the client is not connected."""
    client = ImapClient(cfg)
    with pytest.raises(ImapError, match="Not connected"):
        client.select_folder("INBOX")


def test_select_folder_non_ok_status(cfg: MailConfig) -> None:
    """select_folder raises ImapError on non-OK SELECT response."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.select.return_value = ("NO", [])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            with pytest.raises(ImapError, match="SELECT 'INBOX' failed: NO"):
                client.select_folder("INBOX")


def test_select_folder_non_numeric_count(cfg: MailConfig) -> None:
    """select_folder returns 0 when the SELECT count is non-numeric."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.select.return_value = ("OK", [b"foo"])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            count = client.select_folder("INBOX")

    assert count == 0


# ---------------------------------------------------------------------------
# create_folder
# ---------------------------------------------------------------------------


def test_create_folder_success(cfg: MailConfig) -> None:
    """create_folder issues CREATE and returns None on OK."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.create.return_value = ("OK", [b"Create completed"])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            client.create_folder("robotsix-mail-archive")

    mock_ssl.create.assert_called_once_with("robotsix-mail-archive")
    mock_ssl.subscribe.assert_called_once_with("robotsix-mail-archive")


def test_create_folder_not_connected(cfg: MailConfig) -> None:
    """create_folder raises ImapError when the client is not connected."""
    client = ImapClient(cfg)
    with pytest.raises(ImapError, match="Not connected"):
        client.create_folder("robotsix-mail-archive")


def test_create_folder_genuine_failure(cfg: MailConfig) -> None:
    """Non-OK status with the folder absent from LIST raises ImapError."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.create.return_value = ("NO", [b"Permission denied"])
    mock_ssl.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            with pytest.raises(ImapError, match="CREATE 'Archive' failed"):
                client.create_folder("Archive")

    mock_ssl.subscribe.assert_not_called()


def test_create_folder_genuine_failure_includes_response_text(
    cfg: MailConfig,
) -> None:
    """Genuine failure error message includes the server's response text."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.create.return_value = ("NO", [b"Permission denied (Failure)"])
    mock_ssl.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            with pytest.raises(
                ImapError,
                match=r"CREATE 'Archive' failed: NO — Permission denied",
            ):
                client.create_folder("Archive")

    mock_ssl.subscribe.assert_not_called()


def test_create_folder_already_exists_is_idempotent(cfg: MailConfig) -> None:
    """Non-OK with ALREADYEXISTS in response data → returns without LIST."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.create.return_value = (
        "NO",
        [b"[ALREADYEXISTS] Mailbox already exists. (Failure)"],
    )

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            client.create_folder("robotsix-mail-archive")

    # ALREADYEXISTS was detected in the response data - no LIST needed.
    mock_ssl.list.assert_not_called()
    mock_ssl.subscribe.assert_called_once_with("robotsix-mail-archive")


def test_create_folder_no_status_in_list_still_ok(cfg: MailConfig) -> None:
    """Non-OK without ALREADYEXISTS text but folder IS in LIST → returns."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.create.return_value = ("NO", [b"Permission denied"])
    mock_ssl.list.return_value = (
        "OK",
        [b'(\\HasNoChildren) "/" "robotsix-mail-archive"'],
    )

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            client.create_folder("robotsix-mail-archive")

    # LIST was called because the response didn't contain ALREADYEXISTS text.
    mock_ssl.list.assert_called_once()
    mock_ssl.subscribe.assert_called_once_with("robotsix-mail-archive")


def test_create_folder_subscribe_failure_is_graceful(
    cfg: MailConfig,
) -> None:
    """CREATE OK but SUBSCRIBE fails → error is caught, create_folder succeeds."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.create.return_value = ("OK", [b"Create completed"])
    mock_ssl.subscribe.side_effect = imaplib.IMAP4.error("SUBSCRIBE failed")

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            client.create_folder("robotsix-mail-archive")

    mock_ssl.subscribe.assert_called_once_with("robotsix-mail-archive")


# _is_waste_folder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Trash",
        "Deleted Items",
        "Deleted Messages",
        "Bin",
        "Papierkorb",
        "Gelöschte Objekte",
        "Éléments supprimés",
        "Elementi eliminati",
    ],
)
def test_is_waste_folder_matches_trash(name: str) -> None:
    """_is_waste_folder returns True for known Trash-folder patterns."""
    assert _is_waste_folder(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "Junk",
        "Spam",
        "Bulk Mail",
        "Junk E-mail",
        "Courrier indésirable",
    ],
)
def test_is_waste_folder_matches_junk(name: str) -> None:
    """_is_waste_folder returns True for known Junk/Spam-folder patterns."""
    assert _is_waste_folder(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "INBOX",
        "Projects",
        "Sent",
        "Archive",
        "Drafts",
        "",
        "Inbox",  # not a substring match — "inbox" does not contain any pattern
    ],
)
def test_is_waste_folder_rejects_normal_names(name: str) -> None:
    """_is_waste_folder returns False for non-waste folder names."""
    assert _is_waste_folder(name) is False


# ---------------------------------------------------------------------------
# select_folder_and_uidvalidity
# ---------------------------------------------------------------------------


def test_select_folder_and_uidvalidity_returns_pair(cfg: MailConfig) -> None:
    """Returns (message_count, uidvalidity) parsed from SELECT + UIDVALIDITY."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.select.return_value = ("OK", [b"42"])
    mock_ssl.response.return_value = ("UIDVALIDITY", [b"1234567890"])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            count, uidvalidity = client.select_folder_and_uidvalidity("INBOX")

    assert count == 42
    assert uidvalidity == 1234567890
    mock_ssl.response.assert_called_with("UIDVALIDITY")


def test_select_folder_and_uidvalidity_none_when_absent(cfg: MailConfig) -> None:
    """uidvalidity is None when the server doesn't advertise it."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.select.return_value = ("OK", [b"1"])
    mock_ssl.response.return_value = ("UIDVALIDITY", [None])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            count, uidvalidity = client.select_folder_and_uidvalidity("INBOX")

    assert count == 1
    assert uidvalidity is None


def test_select_folder_and_uidvalidity_none_when_unparseable(
    cfg: MailConfig,
) -> None:
    """uidvalidity is None when the advertised value is not an integer."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.select.return_value = ("OK", [b"1"])
    mock_ssl.response.return_value = ("UIDVALIDITY", [b"not-a-number"])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            _count, uidvalidity = client.select_folder_and_uidvalidity("INBOX")

    assert uidvalidity is None
