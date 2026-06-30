"""MSAL-backed OAuth2 token provider for Microsoft 365 (XOAUTH2).

Microsoft 365 rejects password-based IMAP/SMTP auth and requires an
OAuth2 access token presented over SASL XOAUTH2.  Access tokens expire
after ~1h, so long-running processes need silent refresh.  This module
wraps the optional ``msal`` dependency (installed via the
``robotsix-auto-mail[microsoft]`` extra) to:

* run the device-code consent flow once (works headless / in containers),
* persist the MSAL token cache in the per-account data folder so refresh
  tokens survive restarts,
* hand out fresh access tokens via ``acquire_token_silent`` thereafter.

``msal`` is imported lazily (inside the factory functions) so the package
imports and all existing tests run with ``msal`` NOT installed.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from robotsix_auto_mail.config import ConfigurationError, MailConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    import msal

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: OAuth2 scope granting IMAP access on behalf of the signed-in user.
MICROSOFT_IMAP_SCOPE = "https://outlook.office365.com/IMAP.AccessAsUser.All"

#: OAuth2 scope granting SMTP send on behalf of the signed-in user.
MICROSOFT_SMTP_SCOPE = "https://outlook.office365.com/SMTP.Send"

#: Resource scopes passed to MSAL.  MSAL automatically adds the reserved
#: ``offline_access``/``openid``/``profile`` scopes (and RAISES if they are
#: passed explicitly), so they are intentionally omitted here even though
#: ``offline_access`` is what makes refresh-token issuance work.
MICROSOFT_SCOPES = [MICROSOFT_IMAP_SCOPE, MICROSOFT_SMTP_SCOPE]

#: Well-known public client id usable for IMAP/SMTP XOAUTH2 device-code
#: flow (Thunderbird's registered client id).  Overridable per account via
#: ``auth.oauth2_client_id`` for orgs with their own app registration.
DEFAULT_PUBLIC_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"

#: Identifies the MSAL provider in ``MailConfig.oauth2_provider``.
MICROSOFT_PROVIDER = "microsoft"

#: A zero-arg callable returning a current OAuth2 access token.
TokenProvider = Callable[[], str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_msal() -> Any:
    """Import and return the ``msal`` module, or raise a clear error.

    ``msal`` is an optional dependency behind the ``[microsoft]`` extra.
    """
    try:
        import msal
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise ConfigurationError(
            "Microsoft OAuth2 (oauth2_provider='microsoft') requires the "
            "'msal' package, which is not installed. Install it with: "
            "pip install 'robotsix-auto-mail[microsoft]'"
        ) from exc
    return msal


def _resolve_client_id(config: MailConfig) -> str:
    """Return the MSAL client id: the configured one, else the default."""
    return config.oauth2_client_id or DEFAULT_PUBLIC_CLIENT_ID


def cache_path_for(config: MailConfig) -> Path:
    """Return the MSAL token-cache path for *config*'s account.

    Derived from ``config.db_path`` (``.data/<account-id>/mail.db`` for
    multi-account configs): the cache lives at ``msal_cache.json`` in the
    same per-account data folder.
    """
    return Path(config.db_path).parent / "msal_cache.json"


def _load_cache(config: MailConfig) -> Any:
    """Load (or create) the per-account ``SerializableTokenCache``."""
    msal = _require_msal()
    cache = msal.SerializableTokenCache()
    path = cache_path_for(config)
    if path.exists():
        cache.deserialize(path.read_text())
    return cache


def _persist_cache(config: MailConfig, cache: Any) -> None:
    """Write *cache* back to disk when its state changed."""
    if not cache.has_state_changed:
        return
    path = cache_path_for(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cache.serialize())


# ---------------------------------------------------------------------------
# XOAUTH2 error parsing & CAE support
# ---------------------------------------------------------------------------

_CAE_CLAIMS_RE = re.compile(r'claims="([A-Za-z0-9+/=_-]+)"')

#: AADSTS error codes that indicate a tenant Conditional Access / CAE
#: policy block rather than a plain credential or expiry issue.
_CONDITIONAL_ACCESS_CODES: frozenset[int] = frozenset(
    {
        50097,  # Device compliance required by CA policy
        50158,  # External security challenge not satisfied
        53003,  # Access blocked by CA policy
        50173,  # Token freshness requirement not met (CAE)
        70011,  # Invalid scope requested
        900432,  # Cross-cloud IMAP not supported
    }
)


def parse_xoauth2_error(challenge_bytes: bytes) -> dict[str, Any]:
    """JSON-parse a raw XOAUTH2 server rejection challenge.

    imaplib / smtplib already base64-decode the challenge before passing it
    to the SASL callback, so only JSON parsing is needed here.  Returns an
    empty dict on any parse failure.
    """
    import json

    try:
        return dict(json.loads(challenge_bytes.strip(b"\x00").decode()))
    except Exception:
        return {}


def extract_cae_claims(error_info: dict[str, Any]) -> str | None:
    """Extract a CAE claims-challenge value from a parsed XOAUTH2 error dict.

    Returns the raw base64 claims string if present, else ``None``.
    """
    schemes = error_info.get("schemes", "")
    m = _CAE_CLAIMS_RE.search(schemes)
    return m.group(1) if m else None


def acquire_fresh_token(
    config: MailConfig,
    *,
    claims_challenge: str | None = None,
) -> str:
    """Force-refresh the MSAL access token for *config*'s account.

    Passes *claims_challenge* to ``acquire_token_silent`` when provided
    (Continuous Access Evaluation flow).  Raises ``ConfigurationError``
    when no cached account exists or the refresh fails.
    """
    app = build_msal_app(config)
    accounts = app.get_accounts()
    account_hint = config.username or "<id>"
    if not accounts:
        raise ConfigurationError(
            "No cached Microsoft credentials for "
            f"{account_hint!r}. Run "
            "`robotsix-auto-mail auth login --account <id>` to consent."
        )
    result = app.acquire_token_silent(
        MICROSOFT_SCOPES,
        account=accounts[0],
        force_refresh=True,
        claims_challenge=claims_challenge,
    )
    _persist_cache(config, app.token_cache)
    if not result or "access_token" not in result:
        raise ConfigurationError(
            "Microsoft token force-refresh failed (cache missing or expired). "
            "Run `robotsix-auto-mail auth login --account <id>` to re-consent."
        )
    return str(result["access_token"])


def classify_xoauth2_auth_error(
    challenge_bytes: bytes,
    *,
    username: str,
    host: str,
    port: int,
) -> str:
    """Return a human-readable auth-failure message classified from
    a raw XOAUTH2 rejection challenge.

    Used by IMAP and SMTP clients to produce actionable ``account_health``
    error text when a force-refresh retry also fails.
    """
    error_info = parse_xoauth2_error(challenge_bytes)
    codes: list[int] = error_info.get("error_codes", [])
    base = f"Authentication failed for user {username!r} on {host}:{port}"
    if any(c in _CONDITIONAL_ACCESS_CODES for c in codes):
        code_str = ", ".join(f"AADSTS{c}" for c in codes)
        return (
            f"{base}: Microsoft Conditional Access or tenant policy rejected "
            f"the token even after force-refresh ({code_str}). "
            "Contact your IT admin to allow IMAP/SMTP from this server, "
            "or re-run `robotsix-auto-mail auth login --account <id>` to "
            "re-consent."
        )
    if codes:
        code_str = ", ".join(f"AADSTS{c}" for c in codes)
        return (
            f"{base}: Microsoft OAuth2 token rejected after force-refresh "
            f"({code_str}). "
            "Re-run `robotsix-auto-mail auth login --account <id>` to "
            "re-consent."
        )
    return (
        f"{base}: Microsoft OAuth2 token rejected after force-refresh. "
        "Re-run `robotsix-auto-mail auth login --account <id>` to re-consent."
    )


# ---------------------------------------------------------------------------
# MSAL application + flows
# ---------------------------------------------------------------------------


def build_msal_app(config: MailConfig) -> msal.PublicClientApplication:
    """Construct a ``PublicClientApplication`` backed by the account cache."""
    msal = _require_msal()
    cache = _load_cache(config)
    return msal.PublicClientApplication(
        client_id=_resolve_client_id(config),
        authority=f"https://login.microsoftonline.com/{config.oauth2_tenant}",
        token_cache=cache,
    )


def device_code_login(
    config: MailConfig,
    *,
    on_prompt: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Run the device-code consent flow for *config*'s account.

    Prints the verification URL + code to stderr (and/or invokes
    *on_prompt* with the raw flow dict so callers can customise output),
    then blocks until the user completes consent.  On success the refresh
    token is persisted in the per-account MSAL cache.
    """
    import sys

    msal = _require_msal()
    cache = _load_cache(config)
    app = msal.PublicClientApplication(
        client_id=_resolve_client_id(config),
        authority=f"https://login.microsoftonline.com/{config.oauth2_tenant}",
        token_cache=cache,
    )

    flow = app.initiate_device_flow(scopes=MICROSOFT_SCOPES)
    if "user_code" not in flow:
        raise ConfigurationError(
            "Failed to start Microsoft device-code flow: "
            f"{flow.get('error_description', flow)}"
        )

    if on_prompt is not None:
        on_prompt(flow)
    else:
        print(flow["message"], file=sys.stderr)

    result = app.acquire_token_by_device_flow(flow)
    _persist_cache(config, cache)

    if "access_token" not in result:
        raise ConfigurationError(
            "Microsoft device-code login failed: "
            f"{result.get('error_description', result)}"
        )


def build_token_provider(config: MailConfig) -> TokenProvider | None:
    """Return a token provider for *config*, or ``None`` when not MSAL.

    Returns ``None`` unless ``oauth2_provider == 'microsoft'`` (callers then
    fall back to the static ``oauth2_token`` / password paths).  Otherwise
    returns a zero-arg callable that acquires a fresh access token via
    ``acquire_token_silent`` on each call, refreshing transparently.
    """
    if config.oauth2_provider != MICROSOFT_PROVIDER:
        return None

    account_hint = config.username or "<id>"

    def _provider() -> str:
        app = build_msal_app(config)
        accounts = app.get_accounts()
        if not accounts:
            raise ConfigurationError(
                "No cached Microsoft credentials for "
                f"{account_hint!r}. Run "
                "`robotsix-auto-mail auth login --account <id>` to consent."
            )
        result = app.acquire_token_silent(MICROSOFT_SCOPES, account=accounts[0])
        _persist_cache(config, app.token_cache)
        if not result or "access_token" not in result:
            raise ConfigurationError(
                "Microsoft token refresh failed (cache missing or expired). "
                "Run `robotsix-auto-mail auth login --account <id>` to "
                "re-consent."
            )
        return str(result["access_token"])

    return _provider
