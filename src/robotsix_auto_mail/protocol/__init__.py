"""Shared base class for protocol clients (IMAP, SMTP).

Holds the common config fields (host, port, tls_mode, username,
password), provides a generic ``__repr__``, and dispatches the
three TLS-connection paths via abstract methods that each concrete
client implements with its own protocol library.
"""

from __future__ import annotations

import abc
from collections.abc import Callable


def build_xoauth2_response(username: str, token: str) -> str:
    """Return the SASL XOAUTH2 response string for *username*/*token*."""
    return f"user={username}\x01auth=Bearer {token}\x01\x01"


class _ProtocolClient(abc.ABC):
    """Base class for IMAP and SMTP clients.

    Stores the five config fields shared by both protocols and
    provides the TLS-mode dispatch loop.  Subclasses implement
    ``_connect_direct_tls``, ``_connect_starttls``, and
    ``_connect_plain`` using their protocol-specific libraries
    and exception types.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        tls_mode: str,
        username: str,
        password: str,
        oauth2_token: str = "",
        oauth2_client_id: str = "",
        oauth2_client_secret: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self._tls_mode = tls_mode
        self._username = username
        self._password = password
        self._oauth2_token = oauth2_token
        self._oauth2_client_id = oauth2_client_id
        self._oauth2_client_secret = oauth2_client_secret
        # A dynamic token provider (e.g. MSAL) is wired in by subclasses
        # after ``super().__init__``; ``None`` for static-token / password
        # setups.
        self._token_provider: Callable[[], str] | None = None

    # -- repr --------------------------------------------------------------

    def __repr__(self) -> str:
        cls = type(self).__name__
        return (
            f"{cls}(host={self._host!r}, port={self._port!r}, "
            f"user={self._username!r}, password=<redacted>)"
        )

    # -- TLS dispatch ------------------------------------------------------

    def _dispatch_tls(self) -> None:
        """Dispatch to the appropriate TLS connection helper.

        Raises:
            ValueError: When ``tls_mode`` is not one of the recognised
                values (``direct-tls``, ``starttls``, ``none``).
        """
        tls_mode = self._tls_mode

        if tls_mode == "direct-tls":
            self._connect_direct_tls()
        elif tls_mode == "starttls":
            self._connect_starttls()
        elif tls_mode == "none":
            self._connect_plain()
        else:
            raise ValueError(f"Unknown TLS mode: {tls_mode!r}")

    # -- abstract connection helpers ---------------------------------------

    @abc.abstractmethod
    def _connect_direct_tls(self) -> None: ...

    @abc.abstractmethod
    def _connect_starttls(self) -> None: ...

    @abc.abstractmethod
    def _connect_plain(self) -> None: ...
