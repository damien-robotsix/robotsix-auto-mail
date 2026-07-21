from robotsix_auto_mail.errors import RobotsixMailError

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ImapError(RobotsixMailError):
    """Base exception for all IMAP client errors."""


class ImapConnectionError(ImapError):
    """Socket-level or IMAP greeting failure.

    Wraps ``OSError`` / ``socket.gaierror`` (unreachable host, connection
    refused, timeout) and ``imaplib.IMAP4.error`` from a bad server greeting.
    """


class ImapTlsError(ImapError):
    """TLS negotiation failure.

    Wraps ``STARTTLS`` capability-not-advertised, TLS handshake errors
    (``ssl.SSLError``), and protocol errors during the STARTTLS exchange.
    """


class ImapAuthError(ImapError):
    """Authentication failure.

    Wraps ``imaplib.IMAP4.error`` raised by ``login()`` when the server
    responds with ``'NO'`` or ``'BAD'``.
    """


class ImapMessageNotFoundError(ImapError):
    """The target UID does not exist in the selected folder (stale UID)."""
