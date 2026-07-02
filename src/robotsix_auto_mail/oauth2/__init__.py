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
    path.parent.chmod(0o700)
    path.write_text(cache.serialize())
    path.chmod(0o600)


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


# ---------------------------------------------------------------------------
# XOAUTH2 error parsing + CAE claims extraction
# ---------------------------------------------------------------------------


def parse_xoauth2_error(challenge: bytes) -> dict[str, Any]:
    """Parse the XOAUTH2 server challenge into a dict of error info.

    The challenge is a JSON blob the server sends on authentication
    failure.  Returns ``{}`` when the challenge cannot be decoded as
    JSON (e.g. plain-text error, empty, or malformed).
    """
    import json

    if not challenge:
        return {}
    try:
        text = challenge.decode("utf-8", errors="replace").strip()
        if not text:
            return {}
        result: Any = json.loads(text)
        if not isinstance(result, dict):
            return {}
        return result
    except json.JSONDecodeError, UnicodeDecodeError:
        return {}


def extract_cae_claims(error_info: dict[str, Any]) -> str | None:
    """Extract a CAE claims challenge from a parsed XOAUTH2 error.

    Office365 embeds a ``claims="..."`` parameter inside the
    ``schemes`` field when a token is valid but requires a
    Claims Challenge (Continuous Access Evaluation).  Returns the
    raw claims value, or ``None`` when none is present.
    """
    import re

    schemes = error_info.get("schemes", "")
    if not isinstance(schemes, str) or not schemes:
        return None
    match = re.search(r'claims="([^"]*)"', schemes)
    if match is not None:
        return match.group(1)
    return None


# ---------------------------------------------------------------------------
# Force-refresh token acquisition
# ---------------------------------------------------------------------------


def acquire_fresh_token(
    config: MailConfig,
    *,
    claims_challenge: str | None = None,
) -> str:
    """Force-refresh the MSAL access token, optionally with a CAE claim.

    Only call this when the existing token is known to be invalid
    (e.g. the server rejected it with an XOAUTH2 challenge).  Uses
    ``acquire_token_silent(force_refresh=True)`` to bypass the
    in-memory cache and request a fresh token from Azure AD.

    Args:
        config: The account's ``MailConfig``.
        claims_challenge: Optional CAE claims string extracted from
            the server's rejection payload (see :func:`extract_cae_claims`).

    Returns:
        A new OAuth2 access token.

    Raises:
        ConfigurationError: When no cached MSAL account exists, or the
            refresh itself fails (cache missing, expired, or tenant
            hard-block).
    """
    account_hint = config.username or "<id>"
    app = build_msal_app(config)
    accounts = app.get_accounts()
    if not accounts:
        raise ConfigurationError(
            "Force-refresh failed: no cached Microsoft credentials "
            f"for {account_hint!r}. Run "
            "`robotsix-auto-mail auth login --account <id>` to consent."
        )
    kwargs: dict[str, Any] = dict(
        force_refresh=True,
    )
    if claims_challenge is not None:
        kwargs["claims_challenge"] = claims_challenge
    result = app.acquire_token_silent(MICROSOFT_SCOPES, account=accounts[0], **kwargs)
    _persist_cache(config, app.token_cache)
    if not result or "access_token" not in result:
        raise ConfigurationError(
            "Microsoft token force-refresh failed "
            "(cache missing, expired, or tenant hard-block). "
            "Run `robotsix-auto-mail auth login --account <id>` to "
            "re-consent."
        )
    return str(result["access_token"])


# ---------------------------------------------------------------------------
# Error classification for account_health
# ---------------------------------------------------------------------------

#: Known AADSTS error codes that indicate Conditional Access / CAE blocks
#: (not a credential or setup problem).  These are tenant-policy rejections
#: that cannot be fixed by re-consenting — they require an IT exception.
_CONDITIONAL_ACCESS_AADSTS_CODES: frozenset[str] = frozenset(
    [
        "53000",  # Device not compliant
        "53001",  # Domain not allowed
        "53002",  # App not allowed
        "53003",  # Blocked by Conditional Access
        "53004",  # Proof of possession required
        "530032",  # Device not domain-joined
    ]
)


def classify_xoauth2_auth_error(
    challenge: bytes,
    *,
    username: str,
    host: str,
    port: int,
) -> str:
    """Produce an enriched auth-error message for *account_health*.

    When the server challenge contains a known AADSTS Conditional
    Access code the message explicitly names "Conditional Access" so
    operators can distinguish a tenant-policy block from a credential
    / token-expiry problem.
    """
    error_info = parse_xoauth2_error(challenge)
    # Search the entire challenge text for an AADSTS code, not just a
    # specific JSON field, because Office365 embeds the code in
    # different places (top-level ``error_description``, inside
    # ``schemes``, or as part of a free-text ``error`` field).
    challenge_text = (
        challenge.decode("utf-8", errors="replace")
        if challenge
        else error_info.get("error_description", "")
    )
    found_ca = False
    for code in _CONDITIONAL_ACCESS_AADSTS_CODES:
        if f"AADSTS{code}" in challenge_text:
            found_ca = True
            break

    if found_ca:
        return (
            f"Authentication failed for user {username!r} "
            f"on {host}:{port}: "
            "Conditional Access policy blocked access "
            f"(AADSTS error in server challenge: {challenge_text!r}). "
            "This is a tenant-level policy restriction — it cannot be "
            "fixed by re-consenting.  Contact your Microsoft 365 "
            "administrator to request an exception for this device / "
            "application, or move to a different auth method."
        )

    # Fallback: generic classified message that includes the server
    # challenge text for diagnosis.
    return (
        f"Authentication failed for user {username!r} "
        f"on {host}:{port}. "
        "Token refresh was attempted but the server rejected the "
        f"new token (challenge: {challenge_text!r}). "
        "Run `robotsix-auto-mail auth login --account <id>` to "
        "re-consent, or check the Microsoft 365 tenant's "
        "Conditional Access policies if this persists."
    )


# ---------------------------------------------------------------------------
# Token provider factory
# ---------------------------------------------------------------------------


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
