"""Tests for the SMTP exception hierarchy and repr."""

from __future__ import annotations

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.smtp import (
    SmtpAuthError,
    SmtpClient,
    SmtpConnectionError,
    SmtpError,
    SmtpSendError,
    SmtpTlsError,
)

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


def test_smtp_error_is_exception() -> None:
    """SmtpError is a proper Exception subclass."""
    assert issubclass(SmtpError, Exception)


def test_smtp_connection_error_is_smtp_error() -> None:
    """SmtpConnectionError is a subclass of SmtpError."""
    assert issubclass(SmtpConnectionError, SmtpError)


def test_smtp_tls_error_is_smtp_error() -> None:
    """SmtpTlsError is a subclass of SmtpError."""
    assert issubclass(SmtpTlsError, SmtpError)


def test_smtp_auth_error_is_smtp_error() -> None:
    """SmtpAuthError is a subclass of SmtpError."""
    assert issubclass(SmtpAuthError, SmtpError)


def test_smtp_send_error_is_smtp_error() -> None:
    """SmtpSendError is a subclass of SmtpError."""
    assert issubclass(SmtpSendError, SmtpError)


def test_specific_errors_caught_by_base() -> None:
    """Callers can catch SmtpError to handle all SMTP failure modes."""
    for exc_cls in (
        SmtpConnectionError,
        SmtpTlsError,
        SmtpAuthError,
        SmtpSendError,
    ):
        try:
            raise exc_cls("test")
        except SmtpError:
            pass
        else:
            pytest.fail(f"{exc_cls.__name__} not caught by SmtpError")


# ---------------------------------------------------------------------------
# repr
# ---------------------------------------------------------------------------


def test_repr_redacts_password(cfg: MailConfig) -> None:
    """repr(SmtpClient) must not expose the password."""
    client = SmtpClient(cfg)
    r = repr(client)
    assert "s3cret" not in r
    assert "<redacted>" in r
    assert "smtp.example.com" in r


def test_repr_includes_user(cfg: MailConfig) -> None:
    """repr(SmtpClient) includes the username."""
    client = SmtpClient(cfg)
    r = repr(client)
    assert "user@example.com" in r
