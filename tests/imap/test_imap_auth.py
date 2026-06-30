"""Tests for IMAP authentication: login, XOAUTH2, and Gmail app-password hints."""

from __future__ import annotations

import imaplib
from typing import Any
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


# ---------------------------------------------------------------------------
# XOAUTH2 force-refresh retry (MSAL-managed tokens)
# ---------------------------------------------------------------------------


def test_msal_config_set_when_provider_present() -> None:
    """_msal_config is set when build_token_provider returns a provider."""
    cfg = MailConfig(
        imap_host="outlook.office365.com",
        smtp_host="smtp.office365.com",
        username="user@contoso.com",
        password="",
    )
    provider = mock.Mock(return_value="token-1")
    with mock.patch(
        "robotsix_auto_mail.imap.build_token_provider", return_value=provider
    ):
        client = ImapClient(cfg)
    assert client._msal_config is cfg
    assert client._token_provider is provider


def test_msal_config_none_when_no_provider() -> None:
    """_msal_config is None when no MSAL provider (password-only)."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
    )
    with mock.patch("robotsix_auto_mail.imap.build_token_provider", return_value=None):
        client = ImapClient(cfg)
    assert client._msal_config is None


def test_xoauth2_force_refresh_succeeds_on_retry() -> None:
    """First XOAUTH2 fails → force-refresh → reconnect → retry succeeds."""
    cfg = MailConfig(
        imap_host="outlook.office365.com",
        smtp_host="smtp.office365.com",
        username="user@contoso.com",
        password="",
    )
    provider = mock.Mock(return_value="token-1")
    challenge = b'{"status":"400","schemes":"Bearer"}'
    fresh_token = "token-after-refresh"

    mock_ssl_1 = _make_mock_imap_ssl()

    def fake_auth_fail(mechanism: Any, callback: Any) -> None:
        callback(b"")  # initial response
        callback(challenge)  # server rejection
        raise imaplib.IMAP4.error("AUTHENTICATE failed")

    mock_ssl_1.authenticate.side_effect = fake_auth_fail

    mock_ssl_2 = _make_mock_imap_ssl()
    mock_ssl_2.authenticate.return_value = ("OK", [b"Authenticated"])

    with mock.patch(
        "robotsix_auto_mail.imap.build_token_provider", return_value=provider
    ):
        with mock.patch("imaplib.IMAP4_SSL", side_effect=[mock_ssl_1, mock_ssl_2]):
            with mock.patch(
                "robotsix_auto_mail.oauth2.acquire_fresh_token",
                return_value=fresh_token,
            ) as mock_acquire:
                with ImapClient(cfg):
                    pass

    mock_acquire.assert_called_once()
    provider.assert_called_once()
    # First connection's authenticate was called (and failed)
    assert mock_ssl_1.authenticate.called
    # Second connection's authenticate succeeded
    assert mock_ssl_2.authenticate.called
    mock_ssl_2.login.assert_not_called()


def test_xoauth2_force_refresh_fails_conditional_access() -> None:
    """Both original and force-refreshed tokens rejected with AADSTS53003
    → ImapAuthError mentions Conditional Access."""
    cfg = MailConfig(
        imap_host="outlook.office365.com",
        smtp_host="smtp.office365.com",
        username="user@contoso.com",
        password="",
    )
    provider = mock.Mock(return_value="token-1")
    challenge = b'{"error_description":"AADSTS53003: Blocked by Conditional Access"}'
    fresh_token = "token-after-refresh"

    mock_ssl_1 = _make_mock_imap_ssl()

    def fake_auth_fail_1(mechanism: Any, callback: Any) -> None:
        callback(b"")
        callback(challenge)
        raise imaplib.IMAP4.error("AUTHENTICATE failed")

    mock_ssl_1.authenticate.side_effect = fake_auth_fail_1

    mock_ssl_2 = _make_mock_imap_ssl()

    def fake_auth_fail_2(mechanism: Any, callback: Any) -> None:
        callback(b"")
        callback(challenge)
        raise imaplib.IMAP4.error("AUTHENTICATE failed again")

    mock_ssl_2.authenticate.side_effect = fake_auth_fail_2

    with mock.patch(
        "robotsix_auto_mail.imap.build_token_provider", return_value=provider
    ):
        with mock.patch("imaplib.IMAP4_SSL", side_effect=[mock_ssl_1, mock_ssl_2]):
            with mock.patch(
                "robotsix_auto_mail.oauth2.acquire_fresh_token",
                return_value=fresh_token,
            ):
                with pytest.raises(ImapAuthError) as exc:
                    with ImapClient(cfg):
                        pass

    assert "Conditional Access" in str(exc.value)


def test_xoauth2_no_retry_for_static_token() -> None:
    """Static oauth2_token (no MSAL provider) → no force-refresh, immediate error."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="",
        oauth2_token="ya29.static-token",
    )

    mock_ssl = _make_mock_imap_ssl()
    auth_error = imaplib.IMAP4.error("AUTHENTICATE failed")
    mock_ssl.authenticate.side_effect = auth_error

    with mock.patch("robotsix_auto_mail.imap.build_token_provider", return_value=None):
        with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
            with mock.patch(
                "robotsix_auto_mail.oauth2.acquire_fresh_token"
            ) as mock_acquire:
                with pytest.raises(ImapAuthError):
                    with ImapClient(cfg):
                        pass

    mock_acquire.assert_not_called()


def test_cae_claims_forwarded_to_acquire_fresh_token() -> None:
    """Challenge with CAE claims → acquire_fresh_token receives claims_challenge."""
    cfg = MailConfig(
        imap_host="outlook.office365.com",
        smtp_host="smtp.office365.com",
        username="user@contoso.com",
        password="",
    )
    provider = mock.Mock(return_value="token-1")
    challenge = b'{"status":"400","schemes":"Bearer claims=\\"XYZ123\\""}'
    fresh_token = "token-after-refresh"

    mock_ssl_1 = _make_mock_imap_ssl()

    def fake_auth_fail(mechanism: Any, callback: Any) -> None:
        callback(b"")
        callback(challenge)
        raise imaplib.IMAP4.error("AUTHENTICATE failed")

    mock_ssl_1.authenticate.side_effect = fake_auth_fail

    mock_ssl_2 = _make_mock_imap_ssl()
    mock_ssl_2.authenticate.return_value = ("OK", [b"Authenticated"])

    with mock.patch(
        "robotsix_auto_mail.imap.build_token_provider", return_value=provider
    ):
        with mock.patch("imaplib.IMAP4_SSL", side_effect=[mock_ssl_1, mock_ssl_2]):
            with mock.patch(
                "robotsix_auto_mail.oauth2.acquire_fresh_token",
                return_value=fresh_token,
            ) as mock_acquire:
                with ImapClient(cfg):
                    pass

    mock_acquire.assert_called_once()
    _, kwargs = mock_acquire.call_args
    assert kwargs.get("claims_challenge") == "XYZ123"
