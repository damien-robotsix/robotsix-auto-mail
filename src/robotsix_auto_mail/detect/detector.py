"""Email provider detection — autoconfig, MX-lookup, and conversion helpers.

All the logic that was previously in ``detect/__init__.py``, split out so
``__init__.py`` is a thin re-export facade.
"""

from __future__ import annotations

import json
import urllib.parse
from xml.etree import ElementTree  # nosec B405

import httpx
from pydantic import SecretStr
from robotsix_http import RetryConfig, call_with_retry

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.detect._ssrf_guard import guarded_client
from robotsix_auto_mail.detect.models import (
    MailProvider,
    ProviderEntry,
)

# ---------------------------------------------------------------------------
# Shared HTTP client (sync) with retry configuration
# ---------------------------------------------------------------------------

# SSRF-hardened (robotsix-standards ssrf-hardened-fetchers.md): the
# connection layer rejects non-public resolved addresses and connects to the
# validated IP literal; ``trust_env=False`` so no env proxy is honoured.
_HTTP = guarded_client()

_RETRY_CONFIG = RetryConfig(max_retries=2, backoff_base=2.0, backoff_cap=10.0)


# ---------------------------------------------------------------------------
# Single source-of-truth provider registry
# ---------------------------------------------------------------------------

_PROVIDER_DB: tuple[ProviderEntry, ...] = (
    # ---- consumer / major providers ----
    ProviderEntry(
        label="Gmail / Google Workspace",
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
        mx_needles=("google.com", "googlemail.com"),
    ),
    ProviderEntry(
        label="Outlook / Hotmail / Live / MS365",
        imap_host="outlook.office365.com",
        smtp_host="smtp.office365.com",
        mx_needles=("outlook.com", "office365.com", "protection.outlook.com"),
    ),
    ProviderEntry(
        label="Yahoo Mail",
        imap_host="imap.mail.yahoo.com",
        smtp_host="smtp.mail.yahoo.com",
        mx_needles=("yahoodns.net",),
    ),
    ProviderEntry(
        label="iCloud",
        imap_host="imap.mail.me.com",
        smtp_host="smtp.mail.me.com",
        mx_needles=("icloud.com", "me.com", "mac.com"),
    ),
    ProviderEntry(
        label="Fastmail",
        imap_host="imap.fastmail.com",
        smtp_host="smtp.fastmail.com",
        mx_needles=("messagingengine.com", "fastmail"),
    ),
    ProviderEntry(
        label="Zoho Mail",
        imap_host="imap.zoho.com",
        smtp_host="smtp.zoho.com",
        mx_needles=("zoho.com", "zoho.eu"),
    ),
    ProviderEntry(
        label="GMX",
        imap_host="imap.gmx.com",
        smtp_host="mail.gmx.com",
        mx_needles=("gmx",),
    ),
    ProviderEntry(
        label="mail.com",
        imap_host="imap.mail.com",
        smtp_host="smtp.mail.com",
        mx_needles=("mail.com",),
    ),
    ProviderEntry(
        label="Yandex Mail",
        imap_host="imap.yandex.com",
        smtp_host="smtp.yandex.com",
        mx_needles=("yandex",),
    ),
    ProviderEntry(
        label="QQ Mail",
        imap_host="imap.qq.com",
        smtp_host="smtp.qq.com",
        mx_needles=("qq.com",),
    ),
    ProviderEntry(
        label="AOL Mail",
        imap_host="imap.aol.com",
        smtp_host="smtp.aol.com",
        mx_needles=("aol.com",),
    ),
    ProviderEntry(
        label="Mail.ru",
        imap_host="imap.mail.ru",
        smtp_host="smtp.mail.ru",
        mx_needles=("mail.ru",),
    ),
    # ---- managed hosting of custom domains ----
    ProviderEntry(
        label="mailbox.org",
        imap_host="imap.mailbox.org",
        smtp_host="smtp.mailbox.org",
        mx_needles=("mailbox.org",),
    ),
    ProviderEntry(
        label="Migadu",
        imap_host="imap.migadu.com",
        smtp_host="smtp.migadu.com",
        mx_needles=("migadu.com",),
    ),
    ProviderEntry(
        label="Gandi",
        imap_host="mail.gandi.net",
        smtp_host="mail.gandi.net",
        mx_needles=("gandi.net",),
    ),
    ProviderEntry(
        label="OVH",
        imap_host="ssl0.ovh.net",
        smtp_host="ssl0.ovh.net",
        mx_needles=("ovh.net", "ovh.ca"),
    ),
    ProviderEntry(
        label="Infomaniak",
        imap_host="mail.infomaniak.com",
        smtp_host="mail.infomaniak.com",
        mx_needles=("infomaniak.com",),
    ),
    ProviderEntry(
        label="Purelymail",
        imap_host="imap.purelymail.com",
        smtp_host="smtp.purelymail.com",
        mx_needles=("purelymail.com",),
    ),
    # ---- GoDaddy ----
    ProviderEntry(
        label="GoDaddy",
        imap_host="imap.secureserver.net",
        smtp_host="smtpout.secureserver.net",
        mx_needles=("secureserver.net",),
    ),
)

# ---------------------------------------------------------------------------
# Anti-spam gateway MX needles — map to None (hide the real provider)
# ---------------------------------------------------------------------------

_GATEWAY_MX_NEEDLES: tuple[tuple[str, ...], ...] = (
    ("pphosted.com", "proofpoint", "mimecast", "barracudanetworks.com"),
)

# ---------------------------------------------------------------------------
# Derive _MX_PROVIDERS from the unified registry
# ---------------------------------------------------------------------------


def _build_mx_providers() -> list[tuple[tuple[str, ...], MailProvider | None]]:
    """Build the MX-provider lookup table from ``_PROVIDER_DB``."""
    result: list[tuple[tuple[str, ...], MailProvider | None]] = []
    for entry in _PROVIDER_DB:
        if entry.mx_needles and entry.imap_host:
            result.append(
                (
                    entry.mx_needles,
                    MailProvider(
                        imap_host=entry.imap_host,
                        smtp_host=entry.smtp_host,
                        imap_port=entry.imap_port,
                        imap_tls_mode=entry.imap_tls_mode,
                        smtp_port=entry.smtp_port,
                        smtp_tls_mode=entry.smtp_tls_mode,
                    ),
                )
            )
    for needles in _GATEWAY_MX_NEEDLES:
        result.append((needles, None))
    return result


_MX_PROVIDERS: list[tuple[tuple[str, ...], MailProvider | None]] = _build_mx_providers()

# ---------------------------------------------------------------------------
# Autoconfig (Mozilla ISPDB + domain autoconfig)
# ---------------------------------------------------------------------------

# Mozilla maps Thunderbird "socketType" values to our TLS-mode vocabulary.
_SOCKET_TYPE_TO_TLS = {
    "SSL": "direct-tls",
    "STARTTLS": "starttls",
    "plain": "none",
}


def _autoconfig_urls(email_address: str) -> list[str]:
    """Return candidate autoconfig URLs to try, most authoritative first."""
    domain = email_address.rpartition("@")[2].strip().lower()
    if not domain:
        return []
    quoted = urllib.parse.quote(email_address)
    # Security note: these URLs target autoconfig.<domain> derived from the
    # supplied email, i.e. a caller-influenced host. They are fetched through
    # the SSRF-guarded ``_HTTP`` client, whose connection layer refuses any
    # host resolving to a private/loopback/link-local/metadata/CGNAT address.
    return [
        # Mozilla ISPDB — central database keyed by domain.
        f"https://autoconfig.thunderbird.net/v1.1/{domain}",
        # Provider-hosted autoconfig (the Thunderbird autoconfig protocol).
        f"https://autoconfig.{domain}/mail/config-v1.1.xml?emailaddress={quoted}",
    ]


def _parse_autoconfig_xml(xml_text: str) -> MailProvider | None:
    """Parse a Thunderbird ``clientConfig`` document into a MailProvider.

    Returns ``None`` when the document lacks a usable IMAP + SMTP pair.
    """
    try:
        root = ElementTree.fromstring(xml_text)  # noqa: S314  # nosec B314
    except ElementTree.ParseError:
        return None

    def _server(kind: str, type_attr: str) -> dict[str, str] | None:
        for node in root.iter(kind):
            if node.get("type") == type_attr:
                host = (node.findtext("hostname") or "").strip()
                port = (node.findtext("port") or "").strip()
                socket = (node.findtext("socketType") or "").strip()
                if host:
                    return {"host": host, "port": port, "socket": socket}
        return None

    imap = _server("incomingServer", "imap")
    smtp = _server("outgoingServer", "smtp")
    if imap is None or smtp is None:
        return None

    def _port(value: str, default: int) -> int:
        try:
            return int(value)
        except ValueError:
            return default

    def _tls(socket: str, port: int) -> str:
        mode = _SOCKET_TYPE_TO_TLS.get(socket)
        if mode is not None:
            return mode
        # Fall back to a sensible default based on the port.
        return "direct-tls" if port == 993 else "starttls"

    imap_port = _port(imap["port"], 993)
    smtp_port = _port(smtp["port"], 587)
    return MailProvider(
        imap_host=imap["host"],
        imap_port=imap_port,
        imap_tls_mode=_tls(imap["socket"], imap_port),
        smtp_host=smtp["host"],
        smtp_port=smtp_port,
        smtp_tls_mode=_tls(smtp["socket"], smtp_port),
    )


def autoconfig_lookup(
    email_address: str, *, timeout: float = 5.0
) -> MailProvider | None:
    """Look up IMAP/SMTP settings via published autoconfig.

    Tries the Mozilla ISPDB and the domain's own autoconfig endpoint. Returns
    a :class:`MailProvider` on the first usable hit, or ``None`` if nothing
    resolves (unknown domain, network error, malformed document, …) — callers
    should then fall back to an MX lookup or manual configuration.
    """
    for url in _autoconfig_urls(email_address):
        # SSRF-guarded client; a blocked target raises SSRFError (ValueError).
        try:
            resp = call_with_retry(
                lambda u=url: _HTTP.request("GET", u, timeout=httpx.Timeout(timeout)),
                config=_RETRY_CONFIG,
                what="autoconfig lookup",
            )
            if resp.status_code != 200:
                continue
            xml_text = resp.text
        except httpx.HTTPError, OSError, ValueError:
            continue
        provider = _parse_autoconfig_xml(xml_text)
        if provider is not None:
            return provider
    return None


# ---------------------------------------------------------------------------
# MX-record provider detection (DNS-over-HTTPS)
# ---------------------------------------------------------------------------

# Google's DNS-over-HTTPS JSON resolver — stdlib-only, works in slim images.
_DOH_RESOLVER = "https://dns.google/resolve"


def mx_lookup(email_address: str, *, timeout: float = 5.0) -> list[str]:
    """Return the MX hostnames for the email's domain, lowest preference first.

    Uses DNS-over-HTTPS (so no system resolver or extra dependency is needed)
    and returns an empty list on any failure.
    """
    domain = email_address.rpartition("@")[2].strip().lower()
    if not domain:
        return []
    url = f"{_DOH_RESOLVER}?name={urllib.parse.quote(domain)}&type=MX"
    try:
        resp = call_with_retry(
            lambda: _HTTP.request(
                "GET",
                url,
                timeout=httpx.Timeout(timeout),
                headers={"Accept": "application/dns-json"},
            ),
            config=_RETRY_CONFIG,
            what="MX lookup",
        )
        if resp.status_code != 200:
            return []
        data = json.loads(resp.text)
    except httpx.HTTPError, OSError, ValueError:
        return []
    if not isinstance(data, dict):
        return []

    records: list[tuple[int, str]] = []
    for answer in data.get("Answer") or []:
        if not isinstance(answer, dict) or answer.get("type") != 15:
            continue  # type 15 == MX
        parts = str(answer.get("data", "")).split()
        if len(parts) == 2:
            try:
                preference = int(parts[0])
            except ValueError:
                preference = 999
            host = parts[1]
        else:
            preference, host = 999, (parts[-1] if parts else "")
        host = host.rstrip(".").lower()
        if host:
            records.append((preference, host))

    records.sort(key=lambda item: item[0])
    return [host for _, host in records]


def provider_from_mx(mx_hosts: list[str]) -> MailProvider | None:
    """Map MX hostnames to known provider settings.

    Returns the first real provider match.  Anti-spam gateways (which hide
    the true provider) and unknown hosts yield ``None`` so the caller can
    fall back to autoconfig or manual configuration.
    """
    for host in mx_hosts:
        for needles, provider in _MX_PROVIDERS:
            if provider is not None and any(n in host for n in needles):
                return provider
    return None


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def _build_microsoft_hosts() -> frozenset[str]:
    """Return known Microsoft IMAP/SMTP hosts, derived from ``_PROVIDER_DB``.

    The canonical provider entry supplies ``imap_host`` and ``smtp_host``;
    ``outlook.com`` is included as an alias that autoconfig backends
    may return for consumer Outlook.com mailboxes.
    """
    for entry in _PROVIDER_DB:
        if entry.label == "Outlook / Hotmail / Live / MS365":
            return frozenset({entry.imap_host, entry.smtp_host, "outlook.com"})
    raise AssertionError("Microsoft provider entry not found in _PROVIDER_DB")


#: Microsoft 365 / Outlook.com IMAP & SMTP hosts.  Used to classify a
#: detected provider as Microsoft regardless of which detection backend
#: (autoconfig / MX) produced it, so ``detect`` can write an OAuth2
#: (XOAUTH2) auth block instead of prompting for a (rejected) password.
_MICROSOFT_HOSTS = _build_microsoft_hosts()


def is_microsoft_provider(provider: MailProvider) -> bool:
    """Return ``True`` when *provider* points at Microsoft 365 / Outlook.com.

    The match is host-based and case-insensitive so it works regardless of
    which detection backend (autoconfig / MX) produced the provider:
    a host that exactly equals ``outlook.office365.com``, ``smtp.office365.com``
    or ``outlook.com``, or ends with ``.office365.com``, is Microsoft.
    """
    for host in (provider.imap_host, provider.smtp_host):
        normalized = host.strip().rstrip(".").lower()
        if normalized in _MICROSOFT_HOSTS or normalized.endswith(".office365.com"):
            return True
    return False


def provider_to_config(
    provider: MailProvider,
    username: str,
    password: str = "",  # nosec B107 -- empty default is intentional; provider may not require a password
    db_path: str = "",
) -> MailConfig:
    """Convert a ``MailProvider`` + username (+ optional password) into a
    ``MailConfig``.

    For a Microsoft 365 / Outlook.com provider the result uses MSAL-managed
    OAuth2 (``oauth2_provider="microsoft"``) with an empty password — these
    hosts reject password auth — so the caller seeds the token cache via the
    device-code flow instead.
    """
    if is_microsoft_provider(provider):
        return MailConfig(
            imap_host=provider.imap_host,
            imap_port=provider.imap_port,
            imap_tls_mode=provider.imap_tls_mode,
            smtp_host=provider.smtp_host,
            smtp_port=provider.smtp_port,
            smtp_tls_mode=provider.smtp_tls_mode,
            username=username,
            password=SecretStr(""),  # nosec B106 -- Microsoft uses OAuth2, never a password
            oauth2_provider="microsoft",
            oauth2_tenant="organizations",
            oauth2_client_id="",
            db_path=db_path,
            imap_folder="INBOX",
        )
    return MailConfig(
        imap_host=provider.imap_host,
        imap_port=provider.imap_port,
        imap_tls_mode=provider.imap_tls_mode,
        smtp_host=provider.smtp_host,
        smtp_port=provider.smtp_port,
        smtp_tls_mode=provider.smtp_tls_mode,
        username=username,
        password=SecretStr(password),
        db_path=db_path,
        imap_folder="INBOX",
    )
