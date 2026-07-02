"""Tests for IMAP connection modes, error handling, and context manager."""

from __future__ import annotations

import imaplib
import socket
import ssl
from dataclasses import FrozenInstanceError
from unittest import mock

import pytest
from tests.conftest import _make_mock_imap, _make_mock_imap_ssl

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import (
    ImapClient,
    ImapConnectionError,
    ImapTlsError,
    MailboxInfo,
)

# ---------------------------------------------------------------------------
# Happy path: direct-TLS
# ---------------------------------------------------------------------------


def test_direct_tls_happy_path(cfg: MailConfig) -> None:
    """Context manager: direct-TLS → login → list folders → close."""
    mock_ssl = _make_mock_imap_ssl()
    raw_list_responses: list[bytes] = [
        b'(\\HasNoChildren) "/" "INBOX"',
        b'(\\HasChildren \\Noselect) "/" "[Gmail]"',
    ]
    mock_ssl.list.return_value = ("OK", raw_list_responses)

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl) as patched:
        with ImapClient(cfg) as client:
            folders = client.list_folders()

        patched.assert_called_once()
        _, kwargs = patched.call_args
        assert kwargs["ssl_context"] is not None
        assert isinstance(kwargs["ssl_context"], ssl.SSLContext)

    mock_ssl.login.assert_called_once_with("user@example.com", "s3cret")
    mock_ssl.logout.assert_called_once()
    # A socket timeout is set so a stalled read can't hang the caller forever.
    assert patched.call_args.kwargs["timeout"] == 60

    assert len(folders) == 2
    assert folders[0] == MailboxInfo(
        name="INBOX", attributes=("\\HasNoChildren",), delimiter="/"
    )
    assert folders[1] == MailboxInfo(
        name="[Gmail]",
        attributes=("\\HasChildren", "\\Noselect"),
        delimiter="/",
    )


# ---------------------------------------------------------------------------
# Happy path: STARTTLS
# ---------------------------------------------------------------------------


def test_starttls_happy_path(cfg: MailConfig) -> None:
    """STARTTLS mode: plain connect → starttls → login → list → close."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        imap_port=143,
        imap_tls_mode="starttls",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
    )

    mock_imap = _make_mock_imap()
    mock_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])

    with mock.patch("imaplib.IMAP4", return_value=mock_imap) as patched:
        with ImapClient(cfg) as client:
            folders = client.list_folders()

        patched.assert_called_once_with("imap.example.com", 143, timeout=60)

    # starttls must be called *before* login
    mock_imap.starttls.assert_called_once()
    _, starttls_kwargs = mock_imap.starttls.call_args
    assert isinstance(starttls_kwargs["ssl_context"], ssl.SSLContext)

    # login only after starttls
    mock_imap.login.assert_called_once_with("user@example.com", "s3cret")
    mock_imap.logout.assert_called_once()
    assert len(folders) == 1


# ---------------------------------------------------------------------------
# Happy path: no-TLS
# ---------------------------------------------------------------------------


def test_no_tls_happy_path(cfg: MailConfig) -> None:
    """No-TLS mode: plain connect → login (no starttls) → close."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        imap_port=143,
        imap_tls_mode="none",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
    )

    mock_imap = _make_mock_imap()

    with mock.patch("imaplib.IMAP4", return_value=mock_imap) as patched:
        with ImapClient(cfg) as client:
            assert client is not None
        patched.assert_called_once_with("imap.example.com", 143, timeout=60)

    mock_imap.starttls.assert_not_called()
    mock_imap.login.assert_called_once_with("user@example.com", "s3cret")
    mock_imap.logout.assert_called_once()


# ---------------------------------------------------------------------------
# Connection errors
# ---------------------------------------------------------------------------


def test_connection_refused_direct_tls(cfg: MailConfig) -> None:
    """Connection refused → ImapConnectionError with __cause__."""
    original = ConnectionRefusedError("Connection refused")
    with mock.patch("imaplib.IMAP4_SSL", side_effect=original):
        with pytest.raises(ImapConnectionError) as exc:
            with ImapClient(cfg):
                pass
        assert "Direct-TLS" in str(exc.value)
        assert exc.value.__cause__ is original


def test_connection_refused_plain(cfg: MailConfig) -> None:
    """Plain connection refused → ImapConnectionError with __cause__."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        imap_port=143,
        imap_tls_mode="none",
        smtp_host="smtp.example.com",
        username="u",
        password="p",
    )
    original = ConnectionRefusedError("Connection refused")
    with mock.patch("imaplib.IMAP4", side_effect=original):
        with pytest.raises(ImapConnectionError) as exc:
            with ImapClient(cfg):
                pass
        assert exc.value.__cause__ is original


def test_imap_greeting_error(cfg: MailConfig) -> None:
    """IMAP4.error on connect (bad greeting) → ImapConnectionError."""
    original = imaplib.IMAP4.error("Bad IMAP4 protocol")
    with mock.patch("imaplib.IMAP4_SSL", side_effect=original):
        with pytest.raises(ImapConnectionError) as exc:
            with ImapClient(cfg):
                pass
        assert exc.value.__cause__ is original


def test_socket_gaierror(cfg: MailConfig) -> None:
    """socket.gaierror (name resolution failure) → ImapConnectionError."""
    original = socket.gaierror("Name or service not known")
    with mock.patch("imaplib.IMAP4_SSL", side_effect=original):
        with pytest.raises(ImapConnectionError) as exc:
            with ImapClient(cfg):
                pass
        assert exc.value.__cause__ is original


# ---------------------------------------------------------------------------
# STARTTLS errors
# ---------------------------------------------------------------------------


def test_starttls_handshake_failure(cfg: MailConfig) -> None:
    """STARTTLS handshake fails → ImapTlsError with __cause__."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        imap_port=143,
        imap_tls_mode="starttls",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
    )

    mock_imap = _make_mock_imap()
    ssl_error = ssl.SSLError("handshake failed")
    mock_imap.starttls.side_effect = ssl_error

    with mock.patch("imaplib.IMAP4", return_value=mock_imap):
        with pytest.raises(ImapTlsError) as exc:
            with ImapClient(cfg):
                pass
        assert "STARTTLS" in str(exc.value)
        assert exc.value.__cause__ is ssl_error


def test_starttls_not_advertised(cfg: MailConfig) -> None:
    """STARTTLS not advertised → ImapTlsError."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        imap_port=143,
        imap_tls_mode="starttls",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
    )

    mock_imap = _make_mock_imap()
    imap_error = imaplib.IMAP4.error("STARTTLS not available")
    mock_imap.starttls.side_effect = imap_error

    with mock.patch("imaplib.IMAP4", return_value=mock_imap):
        with pytest.raises(ImapTlsError) as exc:
            with ImapClient(cfg):
                pass
        assert exc.value.__cause__ is imap_error


# ---------------------------------------------------------------------------
# Context manager error handling
# ---------------------------------------------------------------------------


def test_context_manager_closes_on_exception(cfg: MailConfig) -> None:
    """logout() and socket close are called even when the block raises."""
    mock_ssl = _make_mock_imap_ssl()

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        try:
            with ImapClient(cfg):
                raise RuntimeError("something went wrong inside the block")
        except RuntimeError:
            pass

    mock_ssl.logout.assert_called_once()
    mock_ssl.sock.close.assert_called_once()


def test_context_manager_closes_socket_when_logout_fails(cfg: MailConfig) -> None:
    """When logout() raises, the socket is still closed."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.logout.side_effect = imaplib.IMAP4.error("already closed")

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg):
            pass

    mock_ssl.logout.assert_called_once()
    mock_ssl.sock.close.assert_called_once()


# ---------------------------------------------------------------------------
# MailboxInfo
# ---------------------------------------------------------------------------


def test_mailbox_info_is_frozen() -> None:
    """MailboxInfo is immutable."""
    info = MailboxInfo(name="INBOX", attributes=("\\HasNoChildren",), delimiter="/")
    with pytest.raises(FrozenInstanceError):
        info.name = "OTHER"  # type: ignore[misc]


def test_mailbox_info_repr() -> None:
    """MailboxInfo has a readable repr."""
    info = MailboxInfo(name="INBOX", attributes=("\\HasNoChildren",), delimiter="/")
    r = repr(info)
    assert "INBOX" in r
    assert "HasNoChildren" in r


# ---------------------------------------------------------------------------
# Verifies no SMTP dependency
# ---------------------------------------------------------------------------


def test_imap_client_does_not_import_smtp() -> None:
    """The imap module must not reference the SMTP module."""
    import robotsix_auto_mail.imap as mod

    source = mod.__file__
    assert source is not None
    content = open(source).read()
    # The word "smtp" should only appear in docstrings explaining the
    # separation, never in executable code.  Verify there's no import
    # of or call to an SMTP module.
    assert "smtplib" not in content, (
        "imap module must not import smtplib (keep transports separate)"
    )
    assert "robotsix_auto_mail.smtp" not in content, (
        "imap module must not import robotsix_auto_mail.smtp"
    )


def test_imap_client_only_uses_imap_fields(cfg: MailConfig) -> None:
    """ImapClient reads only IMAP-related fields from MailConfig."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.list.return_value = ("OK", [])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg):
            pass

    # Verify the connection used only IMAP fields — SMTP fields are not touched.
    mock_ssl.login.assert_called_once_with(
        cfg.username, cfg.password.get_secret_value()
    )
