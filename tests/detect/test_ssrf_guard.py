"""SSRF guard tests for the provider-detection HTTP client.

robotsix-standards ``ssrf-hardened-fetchers.md`` mandates loopback and
cloud-metadata guard tests: the real ``_HTTP`` client must refuse to
connect to either, at the connection layer, before any socket is opened.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from unittest import mock

import httpcore
import pytest

from robotsix_auto_mail.detect import autoconfig_lookup, detector
from robotsix_auto_mail.detect._ssrf_guard import (
    SSRFError,
    is_blocked_address,
    resolve_and_validate,
)


def _addrinfo(ip: str) -> list[tuple[object, ...]]:
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (ip, 0))]


@pytest.fixture
def inner_connect() -> Iterator[mock.MagicMock]:
    """Patch the wrapped (real) backend so no socket is ever opened."""
    backend = detector._HTTP._transport._pool._network_backend  # type: ignore[attr-defined]
    with mock.patch.object(
        backend._backend,
        "connect_tcp",
        side_effect=httpcore.ConnectError("blocked by test"),
    ) as connect:
        yield connect


def test_client_does_not_trust_env_proxies() -> None:
    assert detector._HTTP.trust_env is False


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/mail/config-v1.1.xml",
        "https://[::1]/mail/config-v1.1.xml",
        "http://localhost/",
    ],
)
def test_loopback_target_is_rejected(url: str, inner_connect: mock.MagicMock) -> None:
    with (
        mock.patch("socket.getaddrinfo", return_value=_addrinfo("127.0.0.1")),
        pytest.raises(SSRFError),
    ):
        detector._HTTP.request("GET", url)
    inner_connect.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://[fd00:ec2::254]/latest/meta-data/",
        "http://[::ffff:169.254.169.254]/",
        "http://[64:ff9b::a9fe:a9fe]/",
        "http://metadata.google.internal/computeMetadata/v1/",
    ],
)
def test_metadata_target_is_rejected(url: str, inner_connect: mock.MagicMock) -> None:
    with (
        mock.patch("socket.getaddrinfo", return_value=_addrinfo("169.254.169.254")),
        pytest.raises(SSRFError),
    ):
        detector._HTTP.request("GET", url)
    inner_connect.assert_not_called()


def test_autoconfig_lookup_never_connects_to_internal_host(
    inner_connect: mock.MagicMock,
) -> None:
    """An email domain resolving to loopback is refused; lookup yields None."""

    def fake_getaddrinfo(
        host: str, *_a: object, **_k: object
    ) -> list[tuple[object, ...]]:
        if host == "autoconfig.evil.example":
            return _addrinfo("127.0.0.1")
        return _addrinfo("93.184.216.34")

    with mock.patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
        assert autoconfig_lookup("user@evil.example") is None
    # Only the public ISPDB host was ever dialled, at its validated IP.
    dialled = {c.args[0] for c in inner_connect.call_args_list}
    assert dialled == {"93.184.216.34"}


def test_public_host_connects_to_validated_ip_literal() -> None:
    with mock.patch("socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
        assert resolve_and_validate("example.com") == "93.184.216.34"


def test_mixed_public_and_private_records_are_rejected() -> None:
    infos = _addrinfo("93.184.216.34") + _addrinfo("10.0.0.1")
    with (
        mock.patch("socket.getaddrinfo", return_value=infos),
        pytest.raises(SSRFError),
    ):
        resolve_and_validate("rebind.example")


@pytest.mark.parametrize(
    ("ip", "blocked"),
    [
        ("127.0.0.1", True),
        ("10.1.2.3", True),
        ("192.168.0.1", True),
        ("172.16.0.1", True),
        ("169.254.169.254", True),
        ("100.64.0.1", True),  # CGNAT
        ("0.0.0.0", True),  # noqa: S104
        ("224.0.0.1", True),
        ("::1", True),
        ("fe80::1", True),
        ("::ffff:127.0.0.1", True),
        ("2002:7f00:1::", True),  # 6to4 wrapping 127.0.0.1
        ("64:ff9b::7f00:1", True),  # NAT64 wrapping 127.0.0.1
        ("93.184.216.34", False),
        ("2606:4700:4700::1111", False),
    ],
)
def test_is_blocked_address(ip: str, blocked: bool) -> None:
    assert is_blocked_address(ip) is blocked
