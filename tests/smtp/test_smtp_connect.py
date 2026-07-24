"""Tests for SMTP client connect() and XOAUTH2 authentication."""

from __future__ import annotations

import smtplib
import socket
import ssl
from typing import Any
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.smtp import (
    SmtpAuthError,
    SmtpClient,
    SmtpConnectionError,
    SmtpTlsError,
)
from tests.conftest import _make_mock_smtp, _make_mock_smtp_ssl

# ===================================================================
# connect() tests
# ===================================================================


# -- direct-tls -----------------------------------------------------------


def test_connect_direct_tls_creates_smtp_ssl(cfg: MailConfig) -> None:
    """connect() with tls_mode='direct-tls' creates SMTP_SSL with
    ssl.create_default_context()."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_tls_mode="direct-tls",
        username="user@example.com",
        password="s3cret",
    )

    mock_smtp = _make_mock_smtp_ssl()

    with mock.patch("smtplib.SMTP_SSL", return_value=mock_smtp) as patched:
        client = SmtpClient(cfg)
        client.connect()

        patched.assert_called_once()
        _, kwargs = patched.call_args
        assert kwargs["context"] is not None
        assert isinstance(kwargs["context"], ssl.SSLContext)
        assert kwargs["timeout"] == 60

    mock_smtp.login.assert_called_once_with("user@example.com", "s3cret")


# -- starttls -------------------------------------------------------------


def test_connect_starttls_creates_plain_smtp_calls_starttls(
    cfg: MailConfig,
) -> None:
    """connect() with tls_mode='starttls' creates plain SMTP,
    calls ehlo_or_helo_if_needed() before and after starttls()."""
    mock_smtp = _make_mock_smtp()

    with mock.patch("smtplib.SMTP", return_value=mock_smtp) as patched:
        client = SmtpClient(cfg)
        client.connect()

        patched.assert_called_once_with("smtp.example.com", 587, timeout=60)

    # ehlo before starttls
    assert mock_smtp.ehlo_or_helo_if_needed.call_count == 2

    # starttls called
    mock_smtp.starttls.assert_called_once()
    _, starttls_kwargs = mock_smtp.starttls.call_args
    assert isinstance(starttls_kwargs["context"], ssl.SSLContext)

    mock_smtp.login.assert_called_once_with("user@example.com", "s3cret")


# -- none -----------------------------------------------------------------


def test_connect_none_creates_plain_smtp_no_tls(cfg: MailConfig) -> None:
    """connect() with tls_mode='none' creates plain SMTP, no TLS."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        smtp_port=25,
        smtp_tls_mode="none",
        username="user@example.com",
        password="s3cret",
    )

    mock_smtp = _make_mock_smtp()

    with mock.patch("smtplib.SMTP", return_value=mock_smtp) as patched:
        client = SmtpClient(cfg)
        client.connect()

        patched.assert_called_once_with("smtp.example.com", 25, timeout=60)

    mock_smtp.starttls.assert_not_called()
    mock_smtp.login.assert_called_once_with("user@example.com", "s3cret")


# -- connection failure ---------------------------------------------------


def test_connect_connection_refused_direct_tls(cfg: MailConfig) -> None:
    """Connection refused on direct-tls → SmtpConnectionError."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_tls_mode="direct-tls",
        username="user@example.com",
        password="s3cret",
    )

    original = ConnectionRefusedError("Connection refused")
    with mock.patch("smtplib.SMTP_SSL", side_effect=original):
        with pytest.raises(SmtpConnectionError) as exc:
            SmtpClient(cfg).connect()
        assert "Direct-TLS" in str(exc.value)
        assert exc.value.__cause__ is original


def test_connect_connection_refused_plain(cfg: MailConfig) -> None:
    """Connection refused on plain → SmtpConnectionError."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        smtp_port=25,
        smtp_tls_mode="none",
        username="u",
        password="p",
    )

    original = ConnectionRefusedError("Connection refused")
    with mock.patch("smtplib.SMTP", side_effect=original):
        with pytest.raises(SmtpConnectionError) as exc:
            SmtpClient(cfg).connect()
        assert exc.value.__cause__ is original


def test_connect_socket_gaierror(cfg: MailConfig) -> None:
    """socket.gaierror → SmtpConnectionError."""
    original = socket.gaierror("Name or service not known")
    with mock.patch("smtplib.SMTP", side_effect=original):
        with pytest.raises(SmtpConnectionError) as exc:
            SmtpClient(cfg).connect()
        assert exc.value.__cause__ is original


def test_connect_ehlo_failure_on_starttls(cfg: MailConfig) -> None:
    """Pre-STARTTLS EHLO failure → SmtpConnectionError."""
    mock_smtp = _make_mock_smtp()
    ehlo_error = smtplib.SMTPException("EHLO failed")
    mock_smtp.ehlo_or_helo_if_needed.side_effect = ehlo_error

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        with pytest.raises(SmtpConnectionError) as exc:
            SmtpClient(cfg).connect()
        assert "EHLO/HELO failed" in str(exc.value)
        assert exc.value.__cause__ is ehlo_error


# -- TLS failure ----------------------------------------------------------


def test_connect_starttls_failure_not_advertised(cfg: MailConfig) -> None:
    """STARTTLS not advertised → SmtpTlsError."""
    mock_smtp = _make_mock_smtp()
    tls_error = smtplib.SMTPException("STARTTLS not available")
    mock_smtp.starttls.side_effect = tls_error

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        with pytest.raises(SmtpTlsError) as exc:
            SmtpClient(cfg).connect()
        assert "STARTTLS" in str(exc.value)
        assert exc.value.__cause__ is tls_error


def test_connect_starttls_ssl_handshake_failure(cfg: MailConfig) -> None:
    """STARTTLS cert validation failure → SmtpTlsError."""
    mock_smtp = _make_mock_smtp()
    ssl_error = ssl.SSLError("certificate verify failed")
    mock_smtp.starttls.side_effect = ssl_error

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        with pytest.raises(SmtpTlsError) as exc:
            SmtpClient(cfg).connect()
        assert "STARTTLS" in str(exc.value)
        assert exc.value.__cause__ is ssl_error


def test_connect_post_starttls_ehlo_failure(cfg: MailConfig) -> None:
    """Post-STARTTLS EHLO failure → SmtpTlsError."""
    mock_smtp = _make_mock_smtp()
    # first ehlo succeeds, starttls succeeds, second ehlo fails
    ehlo_error = smtplib.SMTPException("EHLO failed after TLS")
    mock_smtp.ehlo_or_helo_if_needed.side_effect = [
        (250, b"OK"),
        ehlo_error,
    ]

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        with pytest.raises(SmtpTlsError) as exc:
            SmtpClient(cfg).connect()
        assert "Post-STARTTLS EHLO/HELO" in str(exc.value)
        assert exc.value.__cause__ is ehlo_error


# -- auth failure ---------------------------------------------------------


def test_connect_authentication_rejected(cfg: MailConfig) -> None:
    """login() fails → SmtpAuthError."""
    mock_smtp = _make_mock_smtp()
    auth_error = smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")
    mock_smtp.login.side_effect = auth_error

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        with pytest.raises(SmtpAuthError) as exc:
            SmtpClient(cfg).connect()
        assert "Authentication failed" in str(exc.value)
        assert "user@example.com" in str(exc.value)
        assert exc.value.__cause__ is auth_error


def test_connect_auth_failure_direct_tls(cfg: MailConfig) -> None:
    """login() fails on direct-tls → SmtpAuthError."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_tls_mode="direct-tls",
        username="user@example.com",
        password="s3cret",
    )

    mock_smtp = _make_mock_smtp_ssl()
    auth_error = smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")
    mock_smtp.login.side_effect = auth_error

    with mock.patch("smtplib.SMTP_SSL", return_value=mock_smtp):
        with pytest.raises(SmtpAuthError) as exc:
            SmtpClient(cfg).connect()
        assert exc.value.__cause__ is auth_error


# -- XOAUTH2 --------------------------------------------------------------


def test_xoauth2_auth_called_when_token_present() -> None:
    """When oauth2_token is set, auth('XOAUTH2', ...) is used."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        oauth2_token="ya29.test-token",
        oauth2_client_id="test-client-id",
        oauth2_client_secret="test-client-secret",
    )

    mock_smtp = _make_mock_smtp()
    mock_smtp.auth.return_value = (235, b"2.7.0 Accepted")

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        SmtpClient(cfg).connect()

    mock_smtp.auth.assert_called_once()
    assert mock_smtp.auth.call_args[0][0] == "XOAUTH2"
    # login should not be called when XOAUTH2 is used
    mock_smtp.login.assert_not_called()


def test_xoauth2_authentication_rejected() -> None:
    """When XOAUTH2 fails, SmtpAuthError is raised."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        oauth2_token="ya29.test-token",
    )

    mock_smtp = _make_mock_smtp()
    auth_error = smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")
    mock_smtp.auth.side_effect = auth_error

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        with pytest.raises(SmtpAuthError) as exc:
            SmtpClient(cfg).connect()
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
    mock_smtp = _make_mock_smtp()
    mock_smtp.auth.return_value = (235, b"2.7.0 Accepted")

    with mock.patch(
        "robotsix_auto_mail.smtp.build_token_provider", return_value=provider
    ):
        with mock.patch("smtplib.SMTP", return_value=mock_smtp):
            SmtpClient(cfg).connect()

    cb = mock_smtp.auth.call_args[0][1]
    assert cb() == "user=user@example.com\x01auth=Bearer provider-token\x01\x01"
    provider.assert_called_once()
    mock_smtp.login.assert_not_called()


def test_xoauth2_provider_refreshes_on_reconnect() -> None:
    """Re-entering the context manager fetches a fresh token each time."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
    )

    provider = mock.Mock(side_effect=["token-1", "token-2"])
    mock_smtp = _make_mock_smtp()
    mock_smtp.auth.return_value = (235, b"2.7.0 Accepted")

    with mock.patch(
        "robotsix_auto_mail.smtp.build_token_provider", return_value=provider
    ):
        with mock.patch("smtplib.SMTP", return_value=mock_smtp):
            client = SmtpClient(cfg)
            with client:
                cb = mock_smtp.auth.call_args[0][1]
                assert cb() == "user=user@example.com\x01auth=Bearer token-1\x01\x01"
            with client:
                cb = mock_smtp.auth.call_args[0][1]
                assert cb() == "user=user@example.com\x01auth=Bearer token-2\x01\x01"

    assert provider.call_count == 2
    mock_smtp.login.assert_not_called()


# ---------------------------------------------------------------------------
# XOAUTH2 force-refresh retry (MSAL-managed tokens)
# ---------------------------------------------------------------------------


def test_smtp_xoauth2_force_refresh_succeeds_on_retry() -> None:
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

    mock_smtp_1 = _make_mock_smtp()

    def fake_auth_fail(
        mechanism: Any, callback: Any, initial_response_ok: bool = True
    ) -> None:
        callback(None)  # initial response
        callback(challenge)  # server rejection
        raise smtplib.SMTPAuthenticationError(535, b"Auth failed")

    mock_smtp_1.auth.side_effect = fake_auth_fail

    mock_smtp_2 = _make_mock_smtp()
    mock_smtp_2.auth.return_value = (235, b"2.7.0 Accepted")

    with mock.patch(
        "robotsix_auto_mail.smtp.build_token_provider", return_value=provider
    ):
        with mock.patch("smtplib.SMTP", side_effect=[mock_smtp_1, mock_smtp_2]):
            with mock.patch(
                "robotsix_auto_mail.oauth2.acquire_fresh_token",
                return_value=fresh_token,
            ) as mock_acquire:
                SmtpClient(cfg).connect()

    mock_acquire.assert_called_once()
    provider.assert_called_once()
    assert mock_smtp_1.auth.called
    assert mock_smtp_2.auth.called
    mock_smtp_2.login.assert_not_called()


def test_smtp_xoauth2_force_refresh_fails_conditional_access() -> None:
    """Both original and force-refreshed tokens rejected with AADSTS53003
    → SmtpAuthError mentions Conditional Access."""
    cfg = MailConfig(
        imap_host="outlook.office365.com",
        smtp_host="smtp.office365.com",
        username="user@contoso.com",
        password="",
    )
    provider = mock.Mock(return_value="token-1")
    challenge = b'{"error_description":"AADSTS53003: Blocked by Conditional Access"}'
    fresh_token = "token-after-refresh"

    mock_smtp_1 = _make_mock_smtp()

    def fake_auth_fail_1(
        mechanism: Any, callback: Any, initial_response_ok: bool = True
    ) -> None:
        callback(None)
        callback(challenge)
        raise smtplib.SMTPAuthenticationError(535, b"Auth failed")

    mock_smtp_1.auth.side_effect = fake_auth_fail_1

    mock_smtp_2 = _make_mock_smtp()

    def fake_auth_fail_2(
        mechanism: Any, callback: Any, initial_response_ok: bool = True
    ) -> None:
        callback(None)
        callback(challenge)
        raise smtplib.SMTPAuthenticationError(535, b"Auth failed again")

    mock_smtp_2.auth.side_effect = fake_auth_fail_2

    with mock.patch(
        "robotsix_auto_mail.smtp.build_token_provider", return_value=provider
    ):
        with mock.patch("smtplib.SMTP", side_effect=[mock_smtp_1, mock_smtp_2]):
            with mock.patch(
                "robotsix_auto_mail.oauth2.acquire_fresh_token",
                return_value=fresh_token,
            ):
                with pytest.raises(SmtpAuthError) as exc:
                    SmtpClient(cfg).connect()

    assert "Conditional Access" in str(exc.value)


def test_smtp_xoauth2_no_retry_for_static_token() -> None:
    """Static oauth2_token (no MSAL provider) → no force-refresh, immediate error."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="",
        oauth2_token="ya29.static-token",
    )

    mock_smtp = _make_mock_smtp()
    auth_error = smtplib.SMTPAuthenticationError(535, b"Auth failed")
    mock_smtp.auth.side_effect = auth_error

    with mock.patch("robotsix_auto_mail.smtp.build_token_provider", return_value=None):
        with mock.patch("smtplib.SMTP", return_value=mock_smtp):
            with mock.patch(
                "robotsix_auto_mail.oauth2.acquire_fresh_token"
            ) as mock_acquire:
                with pytest.raises(SmtpAuthError):
                    SmtpClient(cfg).connect()

    mock_acquire.assert_not_called()


def test_smtp_cae_claims_forwarded_to_acquire_fresh_token() -> None:
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

    mock_smtp_1 = _make_mock_smtp()

    def fake_auth_fail(
        mechanism: Any, callback: Any, initial_response_ok: bool = True
    ) -> None:
        callback(None)
        callback(challenge)
        raise smtplib.SMTPAuthenticationError(535, b"Auth failed")

    mock_smtp_1.auth.side_effect = fake_auth_fail

    mock_smtp_2 = _make_mock_smtp()
    mock_smtp_2.auth.return_value = (235, b"2.7.0 Accepted")

    with mock.patch(
        "robotsix_auto_mail.smtp.build_token_provider", return_value=provider
    ):
        with mock.patch("smtplib.SMTP", side_effect=[mock_smtp_1, mock_smtp_2]):
            with mock.patch(
                "robotsix_auto_mail.oauth2.acquire_fresh_token",
                return_value=fresh_token,
            ) as mock_acquire:
                SmtpClient(cfg).connect()

    mock_acquire.assert_called_once()
    _, kwargs = mock_acquire.call_args
    assert kwargs.get("claims_challenge") == "XYZ123"
