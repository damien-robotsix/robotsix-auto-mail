"""Tests for autoconfig_lookup (ISPDB XML parsing) and MX DNS lookup."""

from __future__ import annotations

from unittest import mock

import httpx

from robotsix_auto_mail.config.detect import (
    autoconfig_lookup,
    mx_lookup,
    provider_from_mx,
)

from .conftest import _ISPDB_XML, _MX_JSON, _FakeResp

# ---------------------------------------------------------------------------
# autoconfig_lookup
# ---------------------------------------------------------------------------


def test_autoconfig_lookup_parses_ispdb() -> None:
    """A valid clientConfig document is parsed into a MailProvider."""
    with mock.patch("httpx.Client.request", return_value=_FakeResp(_ISPDB_XML)):
        provider = autoconfig_lookup("user@example.net")
    assert provider is not None
    assert provider.imap_host == "imap.example.net"
    assert provider.imap_port == 993
    assert provider.imap_tls_mode == "direct-tls"  # SSL → direct-tls
    assert provider.smtp_host == "smtp.example.net"
    assert provider.smtp_port == 587
    assert provider.smtp_tls_mode == "starttls"  # STARTTLS → starttls


def test_autoconfig_lookup_network_error_returns_none() -> None:
    """A network failure yields None (caller falls back to the LLM)."""
    with mock.patch(
        "httpx.Client.request",
        side_effect=httpx.HTTPError("no route"),
    ):
        assert autoconfig_lookup("user@example.net") is None


def test_autoconfig_lookup_garbage_returns_none() -> None:
    """A non-XML / unparseable body yields None after trying every URL."""
    with mock.patch("httpx.Client.request", return_value=_FakeResp("not xml at all")):
        assert autoconfig_lookup("user@example.net") is None


def test_autoconfig_lookup_missing_smtp_returns_none() -> None:
    """A document with an IMAP server but no SMTP server is rejected."""
    xml = """\
<clientConfig version="1.1">
  <emailProvider id="x">
    <incomingServer type="imap">
      <hostname>imap.example.net</hostname>
      <port>993</port>
      <socketType>SSL</socketType>
    </incomingServer>
  </emailProvider>
</clientConfig>
"""
    with mock.patch("httpx.Client.request", return_value=_FakeResp(xml)):
        assert autoconfig_lookup("user@example.net") is None


# ---------------------------------------------------------------------------
# mx_lookup / provider_from_mx
# ---------------------------------------------------------------------------


def test_mx_lookup_parses_and_sorts() -> None:
    """MX records are parsed and returned lowest-preference first."""
    with mock.patch("httpx.Client.request", return_value=_FakeResp(_MX_JSON)):
        hosts = mx_lookup("user@example.net")
    assert hosts == ["mx1.example.net", "mx2.example.net"]


def test_mx_lookup_network_error_returns_empty() -> None:
    """A DoH failure yields an empty list."""
    with mock.patch(
        "httpx.Client.request",
        side_effect=httpx.HTTPError("no route"),
    ):
        assert mx_lookup("user@example.net") == []


def test_provider_from_mx_gandi() -> None:
    """A Gandi MX target maps to mail.gandi.net."""
    provider = provider_from_mx(["spool.mail.gandi.net", "fb.mail.gandi.net"])
    assert provider is not None
    assert provider.imap_host == "mail.gandi.net"
    assert provider.smtp_host == "mail.gandi.net"


def test_provider_from_mx_google() -> None:
    """A Google Workspace MX target maps to Gmail settings."""
    provider = provider_from_mx(["aspmx.l.google.com"])
    assert provider is not None
    assert provider.imap_host == "imap.gmail.com"


def test_provider_from_mx_gateway_is_none() -> None:
    """An anti-spam gateway hides the provider, so no mapping is returned."""
    assert provider_from_mx(["mx0.example.pphosted.com"]) is None


def test_provider_from_mx_unknown_is_none() -> None:
    """An unrecognised MX host yields None."""
    assert provider_from_mx(["mail.some-tiny-host.example"]) is None
