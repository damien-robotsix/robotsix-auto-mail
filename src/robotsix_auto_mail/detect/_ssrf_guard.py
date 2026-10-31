"""Connection-layer SSRF guard for the (sync) provider-detection HTTP client.

robotsix-standards ``ssrf-hardened-fetchers.md`` requires every fetcher of
caller-influenced URLs to validate the *resolved* address at the connection
layer and to connect to the validated IP literal, so a DNS answer cannot
change between the check and the connect (DNS rebinding).  ``autoconfig_lookup``
fetches ``https://autoconfig.<domain>/…`` where ``<domain>`` comes from the
supplied email address, so it is in scope.

The fleet's shared helper (:mod:`robotsix_http.safety`) only ships an
*async* backend; the detector uses a sync :class:`httpx.Client`, so this
module provides the equivalent :class:`httpcore.NetworkBackend` wrapper.
It is deliberately stricter than the shared ``_ip_is_blocked``: it rejects
every address that is not globally routable (which covers private,
loopback, link-local / cloud-metadata, CGNAT ``100.64.0.0/10``, reserved
and unspecified ranges), plus multicast, and unwraps IPv4-embedded IPv6
forms (IPv4-mapped, 6to4, Teredo, NAT64 ``64:ff9b::/96`` and
``64:ff9b:1::/48``) before checking.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from typing import Any

import httpcore
import httpx

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_NAT64_NETWORKS = (
    ipaddress.IPv6Network("64:ff9b::/96"),
    ipaddress.IPv6Network("64:ff9b:1::/48"),
)


class SSRFError(ValueError):
    """Raised when an outbound connection target is not a public address."""


def _embedded_ipv4(addr: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """Return the IPv4 address embedded in *addr*, if any."""
    if addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    if addr.sixtofour is not None:
        return addr.sixtofour
    if addr.teredo is not None:
        return addr.teredo[1]
    for net in _NAT64_NETWORKS:
        if addr in net:
            return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return None


def is_blocked_address(ip: str | _IPAddress) -> bool:
    """Return ``True`` when *ip* is not a publicly routable unicast address."""
    addr: _IPAddress = ipaddress.ip_address(ip) if isinstance(ip, str) else ip
    candidates: list[_IPAddress] = [addr]
    if isinstance(addr, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(addr)
        if embedded is not None:
            candidates.append(embedded)
    return any(
        not a.is_global or a.is_multicast or a.is_unspecified for a in candidates
    )


def resolve_and_validate(host: str) -> str:
    """Return a validated IP literal for *host*, or raise :class:`SSRFError`.

    Every resolved address must be public; a name that returns any blocked
    record is rejected outright.
    """
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if is_blocked_address(host):
            raise SSRFError(f"Address {host!r} is a blocked (non-public) address")
        return host

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SSRFError(f"Could not resolve host {host!r}: {exc}") from exc
    addresses = [str(info[4][0]) for info in infos]
    if not addresses:
        raise SSRFError(f"Host {host!r} did not resolve to any address")
    for address in addresses:
        if is_blocked_address(address):
            raise SSRFError(f"Host {host!r} resolves to blocked address {address}")
    return addresses[0]


class _SSRFGuardBackend(httpcore.NetworkBackend):
    """Validate every TCP target, then connect to the validated IP literal.

    TLS SNI / certificate verification still use the original hostname —
    httpcore passes the request origin's host to ``start_tls``.
    """

    def __init__(self, backend: httpcore.NetworkBackend) -> None:
        self._backend = backend

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        safe_ip = resolve_and_validate(host)
        return self._backend.connect_tcp(
            safe_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    def connect_unix_socket(self, *args: Any, **kwargs: Any) -> httpcore.NetworkStream:
        del args, kwargs  # unix sockets are never a legitimate fetch target
        raise SSRFError("Unix-domain-socket connections are blocked by the SSRF guard")

    def sleep(self, seconds: float) -> None:
        self._backend.sleep(seconds)


class SSRFGuardTransport(httpx.HTTPTransport):
    """An :class:`httpx.HTTPTransport` whose network backend blocks SSRF targets."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._pool._network_backend = _SSRFGuardBackend(self._pool._network_backend)


def guarded_client(**kwargs: Any) -> httpx.Client:
    """Build an :class:`httpx.Client` with the SSRF guard and ``trust_env=False``.

    ``trust_env=False`` stops an ambient ``HTTP(S)_PROXY`` / ``ALL_PROXY``
    from routing requests through a proxy, which would bypass the
    connection-layer check (the guard would only see the proxy address).
    """
    return httpx.Client(transport=SSRFGuardTransport(), trust_env=False, **kwargs)
