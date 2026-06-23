"""Tests for IMAP authentication: login, XOAUTH2, and Gmail app-password hints."""

from __future__ import annotations

import imaplib
from unittest import mock

import pytest
from tests.conftest import _make_mock_imap_ssl

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import ImapAuthError, ImapClient

# ---------------------------------------------------------------------------
# Gmail app-password auth hint
# ---------------------------------------------------------------------------


def _gmail_cfg(**overrides: str) -> MailConfig:
    """A Gmail MailConfig with placeholder credentials."""
    base = {
        "imap_host": "imap.gmail.com",
        "smtp_host": "smtp.gmail.com",
        "username": "you@gmail.com",
        "password": "wrong-normal-password",
    }
    base.update(overrides)
    return MailConfig(**base)  # type: ignore[arg-type]


def test_gmail_password_failure_appends_app_password_hint() -> None:
    """A plain-login rejection from Gmail steers the user to an App Password."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.login.side_effect = imaplib.IMAP4.error("Invalid credentials")

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with pytest.raises(ImapAuthError) as excinfo:
            with ImapClient(_gmail_cfg()):
                pass

    message = str(excinfo.value)
    assert "App Password" in message
    assert "myaccount.google.com/apppasswords" in message


def test_non_gmail_password_failure_has_no_gmail_hint() -> None:
    """The Gmail hint is host-specific — other providers don't get it."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.login.side_effect = imaplib.IMAP4.error("Invalid credentials")

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with pytest.raises(ImapAuthError) as excinfo:
            with ImapClient(_gmail_cfg(imap_host="imap.example.com")):
                pass

    assert "App Password" not in str(excinfo.value)


def test_gmail_oauth2_failure_has_no_app_password_hint() -> None:
    """An XOAUTH2 (token) failure is not a wrong-password situation."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.authenticate.side_effect = imaplib.IMAP4.error("AUTHENTICATE failed")

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with pytest.raises(ImapAuthError) as excinfo:
            with ImapClient(_gmail_cfg(password="", oauth2_token="ya29.token")):
                pass

    assert "App Password" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# Authentication errors
# ---------------------------------------------------------------------------


def test_authentication_rejected(cfg: MailConfig) -> None:
    """login() returns 'NO' → ImapAuthError."""
    mock_ssl = _make_mock_imap_ssl()
    auth_error = imaplib.IMAP4.error("AUTHENTICATIONFAILED invalid credentials")
    mock_ssl.login.side_effect = auth_error

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with pytest.raises(ImapAuthError) as exc:
            with ImapClient(cfg):
                pass
        assert "Authentication failed" in str(exc.value)
        assert "user@example.com" in str(exc.value)
        assert exc.value.__cause__ is auth_error


# -- XOAUTH2 --------------------------------------------------------------


def test_xoauth2_authenticate_called_when_token_present() -> None:
    """When oauth2_token is set, authenticate('XOAUTH2', ...) is used."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        oauth2_token="ya29.test-token",
        oauth2_client_id="test-client-id",
        oauth2_client_secret="test-client-secret",
    )

    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.authenticate.return_value = ("OK", [b"Authenticated"])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg):
            pass

    mock_ssl.authenticate.assert_called_once()
    assert mock_ssl.authenticate.call_args[0][0] == "XOAUTH2"
    # login should not be called when XOAUTH2 is used
    mock_ssl.login.assert_not_called()


def test_xoauth2_authentication_rejected() -> None:
    """When XOAUTH2 fails, ImapAuthError is raised."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        oauth2_token="ya29.test-token",
    )

    mock_ssl = _make_mock_imap_ssl()
    auth_error = imaplib.IMAP4.error("AUTHENTICATIONFAILED invalid credentials")
    mock_ssl.authenticate.side_effect = auth_error

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with pytest.raises(ImapAuthError) as exc:
            with ImapClient(cfg):
                pass
        assert "Authentication failed" in str(exc.value)
        assert "user@example.com" in str(exc.value)
        assert exc.value.__cause__ is auth_error


def test_xoauth2_uses_token_provider_over_static() -> None:
    """A token provider is preferred over a static oauth2_token."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        oauth2_token="static-token",
    )

    provider = mock.Mock(return_value="provider-token")
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.authenticate.return_value = ("OK", [b"Authenticated"])

    with mock.patch(
        "robotsix_auto_mail.imap.build_token_provider", return_value=provider
    ):
        with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
            with ImapClient(cfg):
                cb = mock_ssl.authenticate.call_args[0][1]
                assert cb(b"") == (
                    b"user=user@example.com\x01auth=Bearer provider-token\x01\x01"
                )

    provider.assert_called_once()
    mock_ssl.login.assert_not_called()


def test_xoauth2_provider_refreshes_on_reconnect() -> None:
    """Re-entering the context manager fetches a fresh token each time."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
    )

    provider = mock.Mock(side_effect=["token-1", "token-2"])
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.authenticate.return_value = ("OK", [b"Authenticated"])

    with mock.patch(
        "robotsix_auto_mail.imap.build_token_provider", return_value=provider
    ):
        with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
            client = ImapClient(cfg)
            with client:
                cb = mock_ssl.authenticate.call_args[0][1]
                assert cb(b"") == (
                    b"user=user@example.com\x01auth=Bearer token-1\x01\x01"
                )
            with client:
                cb = mock_ssl.authenticate.call_args[0][1]
                assert cb(b"") == (
                    b"user=user@example.com\x01auth=Bearer token-2\x01\x01"
                )

    assert provider.call_count == 2
    mock_ssl.login.assert_not_called()
