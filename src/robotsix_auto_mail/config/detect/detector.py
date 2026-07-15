"""Email provider detection — LLM, autoconfig, MX-lookup, and conversion helpers.

All the logic that was previously in ``detect/__init__.py``, split out so
``__init__.py`` is a thin re-export facade.
"""

from __future__ import annotations

import json
import urllib.parse
from xml.etree import ElementTree  # nosec B405

import urllib3
import urllib3.exceptions

from robotsix_auto_mail.config import (
    MailConfig,
)
from robotsix_auto_mail.config.detect.models import (
    DetectedProvider,
    DetectionError,
    MailProvider,
    ProviderEntry,
)
from robotsix_auto_mail.core._llm_agent import _run_llm_agent

# ---------------------------------------------------------------------------
# Shared connection pool
# ---------------------------------------------------------------------------

_HTTP = urllib3.PoolManager()

# ---------------------------------------------------------------------------
# Single source-of-truth provider registry
# ---------------------------------------------------------------------------

_PROVIDER_DB: tuple[ProviderEntry, ...] = (
    # ---- prompt-table providers (rows 1-13) ----
    ProviderEntry(
        label="Gmail / Google Workspace",
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
        mx_needles=("google.com", "googlemail.com"),
        domain_patterns=("gmail.com", "googlemail.com"),
    ),
    ProviderEntry(
        label="Outlook / Hotmail / Live / MS365",
        imap_host="outlook.office365.com",
        smtp_host="smtp.office365.com",
        mx_needles=("outlook.com", "office365.com", "protection.outlook.com"),
        domain_patterns=(
            "outlook.com",
            "outlook.*",
            "hotmail.com",
            "hotmail.*",
            "live.com",
            "live.*",
            "msn.com",
        ),
    ),
    ProviderEntry(
        label="Yahoo Mail",
        imap_host="imap.mail.yahoo.com",
        smtp_host="smtp.mail.yahoo.com",
        mx_needles=("yahoodns.net",),
        domain_patterns=("yahoo.com", "yahoo.*", "ymail.com", "rocketmail.com"),
    ),
    ProviderEntry(
        label="iCloud",
        imap_host="imap.mail.me.com",
        smtp_host="smtp.mail.me.com",
        mx_needles=("icloud.com", "me.com", "mac.com"),
        domain_patterns=("icloud.com", "me.com", "mac.com"),
    ),
    ProviderEntry(
        label="Fastmail",
        imap_host="imap.fastmail.com",
        smtp_host="smtp.fastmail.com",
        mx_needles=("messagingengine.com", "fastmail"),
        domain_patterns=("fastmail.com", "fastmail.*"),
    ),
    ProviderEntry(
        label="Zoho Mail",
        imap_host="imap.zoho.com",
        smtp_host="smtp.zoho.com",
        mx_needles=("zoho.com", "zoho.eu"),
        domain_patterns=("zoho.com", "zoho.*"),
    ),
    ProviderEntry(
        label="Proton Mail Bridge",
        imap_host="127.0.0.1",
        smtp_host="127.0.0.1",
        imap_port=1143,
        imap_tls_mode="none",
        smtp_port=1025,
        smtp_tls_mode="none",
        domain_patterns=("proton.me", "protonmail.com", "pm.me"),
    ),
    ProviderEntry(
        label="GMX",
        imap_host="imap.gmx.com",
        smtp_host="mail.gmx.com",
        mx_needles=("gmx",),
        domain_patterns=("gmx.com", "gmx.*"),
    ),
    ProviderEntry(
        label="mail.com",
        imap_host="imap.mail.com",
        smtp_host="smtp.mail.com",
        mx_needles=("mail.com",),
        domain_patterns=("mail.com",),
    ),
    ProviderEntry(
        label="Yandex Mail",
        imap_host="imap.yandex.com",
        smtp_host="smtp.yandex.com",
        mx_needles=("yandex",),
        domain_patterns=("yandex.com", "yandex.*"),
    ),
    ProviderEntry(
        label="QQ Mail",
        imap_host="imap.qq.com",
        smtp_host="smtp.qq.com",
        mx_needles=("qq.com",),
        domain_patterns=("qq.com",),
    ),
    ProviderEntry(
        label="AOL Mail",
        imap_host="imap.aol.com",
        smtp_host="smtp.aol.com",
        mx_needles=("aol.com",),
        domain_patterns=("aol.com",),
    ),
    ProviderEntry(
        label="Mail.ru",
        imap_host="imap.mail.ru",
        smtp_host="smtp.mail.ru",
        mx_needles=("mail.ru",),
        domain_patterns=("mail.ru", "inbox.ru", "list.ru", "bk.ru"),
    ),
    # ---- domain-heuristics-only (NetEase — per-domain hosts) ----
    ProviderEntry(
        label="NetEase",
        imap_host="",
        smtp_host="",
        domain_patterns=("126.com", "163.com"),
        in_prompt_table=False,
    ),
    # ---- managed-hosting-only providers (not in prompt table) ----
    ProviderEntry(
        label="mailbox.org",
        imap_host="imap.mailbox.org",
        smtp_host="smtp.mailbox.org",
        mx_needles=("mailbox.org",),
        in_prompt_table=False,
    ),
    ProviderEntry(
        label="Migadu",
        imap_host="imap.migadu.com",
        smtp_host="smtp.migadu.com",
        mx_needles=("migadu.com",),
        in_prompt_table=False,
    ),
    ProviderEntry(
        label="Gandi",
        imap_host="mail.gandi.net",
        smtp_host="mail.gandi.net",
        mx_needles=("gandi.net",),
        in_prompt_table=False,
    ),
    ProviderEntry(
        label="OVH",
        imap_host="ssl0.ovh.net",
        smtp_host="ssl0.ovh.net",
        mx_needles=("ovh.net", "ovh.ca"),
        in_prompt_table=False,
    ),
    ProviderEntry(
        label="Infomaniak",
        imap_host="mail.infomaniak.com",
        smtp_host="mail.infomaniak.com",
        mx_needles=("infomaniak.com",),
        in_prompt_table=False,
    ),
    ProviderEntry(
        label="Purelymail",
        imap_host="imap.purelymail.com",
        smtp_host="smtp.purelymail.com",
        mx_needles=("purelymail.com",),
        in_prompt_table=False,
    ),
    # ---- MX-only (GoDaddy — never in prompt) ----
    ProviderEntry(
        label="GoDaddy",
        imap_host="imap.secureserver.net",
        smtp_host="smtpout.secureserver.net",
        mx_needles=("secureserver.net",),
        in_prompt_table=False,
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
# Build the LLM system prompt from the unified registry
# ---------------------------------------------------------------------------


def _build_domain_heuristic_line(entry: ProviderEntry) -> str:
    """Build a single domain-heuristic bullet for *entry*."""
    patterns = entry.domain_patterns
    quoted = ", ".join(f"`@{p}`" for p in patterns)
    label = entry.label
    # Proton gets a special suffix
    if entry.label == "Proton Mail Bridge":
        return f"- {quoted} → {label} (localhost)."
    return f"- {quoted} → {label} settings."


def _build_prompt_table_row(entry: ProviderEntry) -> str:
    """Build a single Markdown table row for *entry*."""
    return (
        f"| {entry.label} "
        f"| `{entry.imap_host}` "
        f"| {entry.imap_port} "
        f"| `{entry.imap_tls_mode}` "
        f"| `{entry.smtp_host}` "
        f"| {entry.smtp_port} "
        f"| `{entry.smtp_tls_mode}` |"
    )


def _build_system_prompt() -> str:
    """Assemble the full system prompt from the registry + fixed prose."""

    # -- table rows: every entry with in_prompt_table=True and imap_host != "" --
    table_rows = [
        _build_prompt_table_row(e)
        for e in _PROVIDER_DB
        if e.in_prompt_table and e.imap_host
    ]

    # -- domain heuristics: every entry with non-empty domain_patterns,
    #    except NetEase (handled manually below) --
    heuristic_lines = [
        _build_domain_heuristic_line(e)
        for e in _PROVIDER_DB
        if e.domain_patterns and e.label != "NetEase"
    ]

    # -- NetEase manual line --
    netease_line = (
        "- `@126.com`, `@163.com` → NetEase: `imap.126.com`/`imap.163.com` "
        "port 993 `direct-tls`, `smtp.126.com`/`smtp.163.com` port 587 "
        "`starttls`."
    )

    return (
        "You are an email provider configuration expert. Given an email "
        "address, return the correct IMAP and SMTP server settings as a "
        "JSON object.\n"
        "\n"
        "**TLS mode rules:**\n"
        "- `direct-tls`: TLS from the first byte — used on IMAP port 993 "
        "and SMTP port 465.\n"
        "- `starttls`: plain connection upgraded to TLS via STARTTLS — "
        "used on IMAP port 143 and SMTP port 587.\n"
        "- `none`: no TLS — for local/dev only.\n"
        "\n"
        "**Known provider settings (use these exact values when the domain "
        "matches):**\n"
        "\n"
        "| Provider | IMAP Host | IMAP Port | IMAP TLS | SMTP Host | "
        "SMTP Port | SMTP TLS |\n"
        "|---|---|---|---|---|---|---|\n" + "\n".join(table_rows) + "\n"
        "\n"
        "**Domain heuristics (when the domain isn't in the table above):**\n"
        + "\n".join(heuristic_lines)
        + "\n"
        + netease_line
        + "\n"
        "- For self-hosted / custom domains (e.g. `@example.com`): the "
        "typical pattern is `imap.<domain>` port 993 and `smtp.<domain>` "
        "port 587 — but many custom domains are hosted by a managed "
        "provider, so consider these too.\n"
        "\n"
        "**Managed hosting of custom domains (the address domain is NOT "
        "the mail host):**\n"
        "- Google Workspace → `imap.gmail.com` / `smtp.gmail.com`.\n"
        "- Microsoft 365 / Exchange Online → `outlook.office365.com` / "
        "`smtp.office365.com`.\n"
        "- Zoho-hosted → `imap.zoho.com` / `smtp.zoho.com` (or `.eu`/`.in` "
        "regional).\n"
        "- Fastmail-hosted → `imap.fastmail.com` / `smtp.fastmail.com`.\n"
        "- mailbox.org → `imap.mailbox.org` / `smtp.mailbox.org`.\n"
        "- Migadu → `imap.migadu.com` / `smtp.migadu.com`.\n"
        "- Gandi → `mail.gandi.net` (IMAP 993 direct-tls, SMTP 587 "
        "starttls).\n"
        "- OVH → `ssl0.ovh.net` (IMAP 993 direct-tls, SMTP 587 starttls).\n"
        "- Infomaniak → `mail.infomaniak.com`.\n"
        "- Purelymail → `imap.purelymail.com` / `smtp.purelymail.com`.\n"
        "- cPanel/Plesk shared hosting → often `mail.<domain>` or the "
        "server hostname.\n"
        "\n"
        "When the obvious `imap.<domain>` is uncertain, prefer "
        "`mail.<domain>` or the provider patterns above. If you are given "
        "feedback that a previous guess failed, do NOT repeat it — propose "
        "a genuinely different host.\n"
        "\n"
        "Return ONLY a JSON object matching the schema — no explanation, "
        "no markdown fences."
    )


_DETECT_SYSTEM_PROMPT: str = _build_system_prompt()


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------


def detect_provider(
    email_address: str,
    *,
    level: int = 1,
    api_key: str | None = None,
    provider_model: str | None = None,
    feedback: str | None = None,
    mx_hosts: list[str] | None = None,
) -> MailProvider:
    """Detect IMAP/SMTP settings for *email_address* via an LLM.

    Args:
        email_address: The email address to detect provider settings for.
        level: LLM integer tier to use.  ``1`` (cheap, default).
        api_key: OpenRouter API key.  Resolves with the precedence
            ``api_key`` argument → ``LLM_API_KEY`` env var → config file (via
            :func:`~robotsix_auto_mail.core._llm_agent._run_llm_agent`).
        provider_model: LLM backend name (e.g. ``openrouter-deepseek``).
            When ``None`` or empty, the tier-level default model is used
            (resolved from :class:`~robotsix_llmio.config.tier.TierConfig`).
            Callers that want env-var or config-file resolution should
            pre-resolve via
            :func:`~robotsix_auto_mail.config.resolve_llm_provider_model`.
        feedback: Optional description of a previous failed attempt (which
            host was tried and how it failed).  When provided, it is added
            to the prompt so the model can propose a different, non-obvious
            configuration instead of repeating the failed guess.
        mx_hosts: Optional MX hostnames for the domain (see
            :func:`mx_lookup`).  Added to the prompt as a strong hint so the
            model identifies the hosting provider instead of guessing.

    Returns:
        A ``MailProvider`` with the detected settings.

    Raises:
        DetectionError: If the API key is missing, the LLM returns an
            invalid response, or any other error occurs.
    """
    # -- build the user message (+ optional MX hint / refinement feedback) --
    user_message = email_address
    if mx_hosts:
        user_message += (
            "\n\nThe domain's MX records point to: "
            + ", ".join(mx_hosts[:5])
            + "\nIdentify the hosting provider from these MX hosts and return "
            "ITS imap/smtp settings (the mailbox host is usually NOT the "
            "address domain)."
        )
    if feedback:
        user_message += (
            "\n\nThe previous configuration attempt FAILED:\n"
            f"{feedback}\n"
            "Propose a corrected configuration with a DIFFERENT host — "
            "do not repeat the failed guess."
        )

    # -- delegate to shared LLM agent helper --
    detected: DetectedProvider = _run_llm_agent(
        api_key=api_key,
        provider_model=provider_model,
        level=level,
        system_prompt=_DETECT_SYSTEM_PROMPT,
        output_model=DetectedProvider,
        user_message=user_message,
        label="email provider detection",
        what="email provider detection",
        exc_type=DetectionError,
    )

    # -- extract and convert --
    return MailProvider(
        imap_host=detected.imap_host,
        imap_port=detected.imap_port,
        imap_tls_mode=detected.imap_tls_mode,
        smtp_host=detected.smtp_host,
        smtp_port=detected.smtp_port,
        smtp_tls_mode=detected.smtp_tls_mode,
    )


# ---------------------------------------------------------------------------
# Autoconfig (Mozilla ISPDB + domain autoconfig) — no LLM required
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
    # operator-supplied email. A crafted domain could probe internal
    # autoconfig hostnames (mild SSRF). Accepted: operator-initiated only,
    # HTTPS-only probe, no credentials sent, 5 s timeout enforced by caller.
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
    """Look up IMAP/SMTP settings via published autoconfig, without an LLM.

    Tries the Mozilla ISPDB and the domain's own autoconfig endpoint. Returns
    a :class:`MailProvider` on the first usable hit, or ``None`` if nothing
    resolves (unknown domain, network error, malformed document, …) — callers
    should then fall back to :func:`detect_provider`.
    """
    for url in _autoconfig_urls(email_address):
        # See _autoconfig_urls() for the SSRF-risk rationale.
        try:
            resp = _HTTP.request("GET", url, timeout=timeout)
            if resp.status != 200:
                continue
            xml_text = resp.data.decode("utf-8", errors="replace")
        except urllib3.exceptions.HTTPError, OSError, ValueError:
            continue
        provider = _parse_autoconfig_xml(xml_text)
        if provider is not None:
            return provider
    return None


# ---------------------------------------------------------------------------
# MX-record provider detection (DNS-over-HTTPS) — no LLM required
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
        resp = _HTTP.request(
            "GET",
            url,
            timeout=timeout,
            headers={"Accept": "application/dns-json"},
        )
        if resp.status != 200:
            return []
        data = json.loads(resp.data.decode("utf-8", errors="replace"))
    except urllib3.exceptions.HTTPError, OSError, ValueError:
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
    fall back to autoconfig or the LLM.
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
    ``outlook.com`` is included as an alias that autoconfig / LLM backends
    may return for consumer Outlook.com mailboxes.
    """
    for entry in _PROVIDER_DB:
        if entry.label == "Outlook / Hotmail / Live / MS365":
            return frozenset({entry.imap_host, entry.smtp_host, "outlook.com"})
    raise AssertionError("Microsoft provider entry not found in _PROVIDER_DB")


#: Microsoft 365 / Outlook.com IMAP & SMTP hosts.  Used to classify a
#: detected provider as Microsoft regardless of which detection backend
#: (autoconfig / MX / LLM) produced it, so ``detect`` can write an OAuth2
#: (XOAUTH2) auth block instead of prompting for a (rejected) password.
_MICROSOFT_HOSTS = _build_microsoft_hosts()


def is_microsoft_provider(provider: MailProvider) -> bool:
    """Return ``True`` when *provider* points at Microsoft 365 / Outlook.com.

    The match is host-based and case-insensitive so it works regardless of
    which detection backend (autoconfig / MX / LLM) produced the provider:
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
            password="",  # nosec B106 -- Microsoft uses OAuth2, never a password
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
        password=password,
        db_path=db_path,
        imap_folder="INBOX",
    )
