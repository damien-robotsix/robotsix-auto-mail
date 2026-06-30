"""SMTP client built on stdlib ``smtplib``.

Provides ``SmtpClient`` - a context manager that connects to an SMTP
server, negotiates TLS, authenticates, and sends plain-text MIME messages.

Depends only on ``MailConfig`` from ``robotsix_auto_mail.config`` and the
Python standard library (``smtplib``, ``ssl``, ``email``).
"""

from __future__ import annotations

import contextlib
import smtplib
import ssl
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Any

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import _ProtocolClient, build_xoauth2_response
from robotsix_auto_mail.oauth2 import build_token_provider

# Store a reference to SMTPException *before* any mocking can replace
# smtplib.SMTP and turn ``SMTPException`` into a MagicMock attribute.
# Using this reference in except clauses keeps tests reliable.
_SMTP_EXCEPTION = smtplib.SMTPException


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SmtpError(Exception):
    """Base exception for all SMTP client errors."""


class SmtpConnectionError(SmtpError):
    """Socket-level or SMTP connection failure.

    Wraps ``OSError`` / ``socket.gaierror`` (unreachable host, connection
    refused, timeout) and ``smtplib.SMTPException`` from a bad server
    greeting or EHLO/HELO failure.
    """


class SmtpTlsError(SmtpError):
    """TLS negotiation failure.

    Wraps ``STARTTLS`` negotiation failures (``smtplib.SMTPException``
    when the server does not advertise the capability) and TLS handshake
    errors (``ssl.SSLError``).
    """


class SmtpAuthError(SmtpError):
    """Authentication failure.

    Wraps ``smtplib.SMTPException`` raised by ``login()`` when the server
    responds with an authentication error (bad credentials, etc.).
    """


class SmtpSendError(SmtpError):
    """Send failure.

    Wraps ``smtplib.SMTPException`` raised by ``send_message()`` when
    the server rejects the message or the connection is lost mid-send.
    """


# ---------------------------------------------------------------------------
# SmtpClient
# ---------------------------------------------------------------------------


class SmtpClient(_ProtocolClient):
    """Context-managed SMTP client.

    Constructor accepts a ``MailConfig`` and extracts only the
    SMTP-relevant fields (``smtp_host``, ``smtp_port``, ``smtp_tls_mode``,
    ``username``, ``password``).  The IMAP fields are never referenced.

    Typical usage::

        cfg = MailConfig.from_env()
        with SmtpClient(cfg) as client:
            client.send(
                from_addr="bot@example.com",
                to_addr="user@example.com",
                subject="Hello",
                body="World",
            )
    """

    def __init__(self, config: MailConfig) -> None:
        super().__init__(
            host=config.smtp_host,
            port=config.smtp_port,
            tls_mode=config.smtp_tls_mode,
            username=config.username,
            password=config.password,
            oauth2_token=config.oauth2_token,
        )
        self._token_provider = build_token_provider(config)

        # Store config for force-refresh retry when MSAL manages the token.
        # Only set when build_token_provider returned a provider (i.e. the
        # oauth2_provider is "microsoft" and MSAL is available).
        self._msal_config: MailConfig | None = (
            config if self._token_provider is not None else None
        )
        self._xoauth2_challenge: bytes = b""

        self._smtp: smtplib.SMTP | None = None

    # -- read-only server metadata ---------------------------------------

    @property
    def ehlo_response(self) -> bytes | None:
        """Full EHLO response bytes, or ``None`` when not connected."""
        if self._smtp is None:
            return None
        return self._smtp.ehlo_resp

    @property
    def esmtp_features(self) -> dict[str, str]:
        """Copy of ``esmtp_features`` dict, or ``{}`` when not connected."""
        if self._smtp is None:
            return {}
        return dict(self._smtp.esmtp_features)

    # -- public API --------------------------------------------------------

    def connect(self) -> None:
        """Connect, negotiate TLS, and authenticate.

        Raises:
            SmtpConnectionError: Connection refused, host unreachable,
                or bad server greeting.
            SmtpTlsError: STARTTLS negotiation or certificate validation
                failure.
            SmtpAuthError: Login rejected (bad credentials, etc.).
        """
        self._dispatch_tls()
        self._authenticate()

    def send(
        self,
        *,
        from_addr: str,
        to_addr: str,
        subject: str,
        body: str,
        cc: list[str] | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> None:
        """Compose and transmit a plain-text MIME message.

        Args:
            from_addr: ``From`` header value.
            to_addr: ``To`` header value (single recipient).
            subject: ``Subject`` header value.
            body: Plain-text message body (UTF-8).
            cc: Optional Cc recipients.  When non-empty, a ``Cc`` header is
                set and these addresses are added to the SMTP envelope so
                they actually receive the mail.
            in_reply_to: Optional ``In-Reply-To`` header value (the
                original message's ``Message-ID``) for threading.
            references: Optional ``References`` header value for threading.

        Raises:
            SmtpError: The client is not connected.
            SmtpSendError: The server rejected the message.
        """
        if self._smtp is None:
            raise SmtpError("Not connected")

        msg = MIMEText(body, _charset="utf-8")
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        if cc:
            msg["Cc"] = ", ".join(cc)
        if in_reply_to is not None:
            msg["In-Reply-To"] = in_reply_to
        if references is not None:
            msg["References"] = references

        to_addrs = [to_addr, *cc] if cc else [to_addr]

        try:
            self._smtp.send_message(msg, from_addr=from_addr, to_addrs=to_addrs)
        except _SMTP_EXCEPTION as exc:
            raise SmtpSendError(
                f"Failed to send message to {to_addr!r}: {exc}"
            ) from exc

    def close(self) -> None:
        """Disconnect gracefully (best-effort).  Safe to call multiple times."""
        if self._smtp is None:
            return
        with contextlib.suppress(_SMTP_EXCEPTION):
            self._smtp.quit()
        self._smtp = None

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> SmtpClient:
        """Connect + authenticate, returning the ready-to-use client."""
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        """Disconnect, even if an exception occurred."""
        self.close()

    # -- connection helpers ------------------------------------------------

    def _connect_direct_tls(self) -> None:
        ctx = ssl.create_default_context()
        try:
            self._smtp = smtplib.SMTP_SSL(self._host, self._port, context=ctx)
        except (OSError, _SMTP_EXCEPTION) as exc:
            raise SmtpConnectionError(
                f"Direct-TLS connection to {self._host}:{self._port} failed: {exc}"
            ) from exc

    def _connect_starttls(self) -> None:
        # 1. Plain connection.
        try:
            self._smtp = smtplib.SMTP(self._host, self._port)
        except (OSError, _SMTP_EXCEPTION) as exc:
            raise SmtpConnectionError(
                f"Plain connection to {self._host}:{self._port} failed: {exc}"
            ) from exc

        # 2. Post-connect EHLO — the server may advertise STARTTLS
        #    (and possibly other extensions we don't use).
        try:
            self._smtp.ehlo_or_helo_if_needed()
        except _SMTP_EXCEPTION as exc:
            raise SmtpConnectionError(f"EHLO/HELO failed: {exc}") from exc

        # 3. Upgrade to TLS.
        ctx = ssl.create_default_context()
        try:
            self._smtp.starttls(context=ctx)
        except (_SMTP_EXCEPTION, ssl.SSLError) as exc:
            raise SmtpTlsError(
                f"STARTTLS negotiation with {self._host}:{self._port} failed: {exc}"
            ) from exc

        # 4. Post-TLS EHLO — the server may advertise different
        #    extensions after upgrading.
        try:
            self._smtp.ehlo_or_helo_if_needed()
        except _SMTP_EXCEPTION as exc:
            raise SmtpTlsError(f"Post-STARTTLS EHLO/HELO failed: {exc}") from exc

    def _connect_plain(self) -> None:
        try:
            self._smtp = smtplib.SMTP(self._host, self._port)
        except (OSError, _SMTP_EXCEPTION) as exc:
            raise SmtpConnectionError(
                f"Plain (no-TLS) connection to {self._host}:{self._port} failed: {exc}"
            ) from exc

    def _authenticate(self) -> None:
        if self._smtp is None:
            raise RuntimeError("_authenticate() called before _connect_*()")
        self._xoauth2_challenge = b""
        if self._token_provider is not None:
            self._oauth2_token = self._token_provider()
        try:
            if self._token_provider is not None or self._oauth2_token:
                self._smtp.auth(
                    "XOAUTH2", self._smtp_xoauth2_cb, initial_response_ok=True
                )
            else:
                self._smtp.login(self._username, self._password)
        except _SMTP_EXCEPTION as exc:
            # Force-refresh retry: only when MSAL manages the token
            # (self._msal_config is set) — static oauth2_token has no
            # refresh mechanism.
            if self._msal_config is not None:
                self._oauth2_token = self._smtp_force_refresh()
                # Reconnect for a clean post-auth state
                self.close()
                self._dispatch_tls()
                self._xoauth2_challenge = b""
                try:
                    self._smtp.auth(
                        "XOAUTH2", self._smtp_xoauth2_cb, initial_response_ok=True
                    )
                    return  # retry succeeded
                except _SMTP_EXCEPTION as exc2:
                    from robotsix_auto_mail.oauth2 import (
                        classify_xoauth2_auth_error,
                    )

                    raise SmtpAuthError(
                        classify_xoauth2_auth_error(
                            self._xoauth2_challenge,
                            username=self._username,
                            host=self._host,
                            port=self._port,
                        )
                    ) from exc2
            raise SmtpAuthError(
                f"Authentication failed for user {self._username!r} "
                f"on {self._host}:{self._port}: {exc}"
            ) from exc

    def _smtp_force_refresh(self) -> str:
        """Force-refresh the MSAL token, passing any CAE claims extracted
        from the server's XOAUTH2 challenge.  Only called when
        ``self._msal_config`` is set.
        """
        from robotsix_auto_mail.oauth2 import (
            acquire_fresh_token,
            extract_cae_claims,
            parse_xoauth2_error,
        )

        error_info = parse_xoauth2_error(self._xoauth2_challenge)
        claims = extract_cae_claims(error_info)
        return acquire_fresh_token(self._msal_config, claims_challenge=claims)  # type: ignore[arg-type]

    def _smtp_xoauth2_cb(self, challenge: bytes | None = None) -> str:
        """SASL XOAUTH2 callback for ``smtplib.SMTP.auth()``.

        On the initial solicitation (``None``) returns the XOAUTH2
        response string.  On any subsequent challenge (server-side
        error) saves it for retry analysis and cancels with ``\\x01``.
        """
        if challenge is not None:
            self._xoauth2_challenge = challenge
            return "\x01"
        return build_xoauth2_response(self._username, self._oauth2_token)
