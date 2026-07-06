"""Tests for IMAP exception hierarchy and repr."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import (
    ImapAuthError,
    ImapClient,
    ImapConnectionError,
    ImapError,
    ImapTlsError,
)
from tests.conftest import _make_mock_imap_ssl

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


def test_imap_error_is_exception() -> None:
    """ImapError is a proper Exception subclass."""
    assert issubclass(ImapError, Exception)


def test_imap_connection_error_is_imap_error() -> None:
    """ImapConnectionError is a subclass of ImapError."""
    assert issubclass(ImapConnectionError, ImapError)


def test_imap_tls_error_is_imap_error() -> None:
    """ImapTlsError is a subclass of ImapError."""
    assert issubclass(ImapTlsError, ImapError)


def test_imap_auth_error_is_imap_error() -> None:
    """ImapAuthError is a subclass of ImapError."""
    assert issubclass(ImapAuthError, ImapError)


def test_specific_errors_caught_by_base() -> None:
    """Callers can catch ImapError to handle all IMAP failure modes."""
    for exc_cls in (ImapConnectionError, ImapTlsError, ImapAuthError):
        try:
            raise exc_cls("test")
        except ImapError:
            pass
        else:
            pytest.fail(f"{exc_cls.__name__} not caught by ImapError")


# ---------------------------------------------------------------------------
# repr
# ---------------------------------------------------------------------------


def test_repr_redacts_password(cfg: MailConfig) -> None:
    """repr(ImapClient) must not expose the password."""
    client = ImapClient(cfg)
    r = repr(client)
    assert "s3cret" not in r
    assert "<redacted>" in r
    assert "imap.example.com" in r


def test_server_greeting_property(cfg: MailConfig) -> None:
    """server_greeting returns the server welcome when connected, None otherwise."""
    client = ImapClient(cfg)
    assert client.server_greeting is None

    mock_ssl = _make_mock_imap_ssl()
    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as connected:
            assert connected.server_greeting == mock_ssl.welcome


def test_capabilities_property(cfg: MailConfig) -> None:
    """capabilities returns the server capabilities when connected, () otherwise."""
    client = ImapClient(cfg)
    assert client.capabilities == ()

    mock_ssl = _make_mock_imap_ssl()
    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as connected:
            assert connected.capabilities == mock_ssl.capabilities


def test_imap_client_invalid_tls_mode(cfg: MailConfig) -> None:
    """Passing an unknown tls_mode raises ValidationError on construction."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="imap_tls_mode"):
        MailConfig(
            imap_host=cfg.imap_host,
            smtp_host=cfg.smtp_host,
            username=cfg.username,
            password=cfg.password,
            imap_tls_mode="invalid",
        )
