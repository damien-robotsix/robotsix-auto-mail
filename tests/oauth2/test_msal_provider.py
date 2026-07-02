"""Tests for the MSAL-backed OAuth2 token provider.

``msal`` is an optional dependency and is NOT installed in the test
environment, so these tests inject a fake ``msal`` module into
``sys.modules`` to exercise the provider logic without the real library.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from robotsix_auto_mail.config import ConfigurationError, MailConfig
from robotsix_auto_mail.oauth2 import (
    MICROSOFT_IMAP_SCOPE,
    MICROSOFT_SMTP_SCOPE,
    acquire_fresh_token,
    build_token_provider,
    cache_path_for,
    classify_xoauth2_auth_error,
    extract_cae_claims,
    parse_xoauth2_error,
)


def _make_config(tmp_path: Path, **overrides: Any) -> MailConfig:
    base: dict[str, Any] = {
        "imap_host": "outlook.office365.com",
        "smtp_host": "smtp.office365.com",
        "username": "user@contoso.com",
        "password": "",
        "db_path": str(tmp_path / "acct" / "mail.db"),
    }
    base.update(overrides)
    return MailConfig(**base)


# ---------------------------------------------------------------------------
# Fake msal module
# ---------------------------------------------------------------------------


class _FakeCache:
    def __init__(self) -> None:
        self.has_state_changed = False
        self._state = ""

    def deserialize(self, text: str) -> None:
        self._state = text

    def serialize(self) -> str:
        return self._state


class _FakeApp:
    def __init__(
        self,
        *,
        accounts: list[Any],
        silent_result: dict[str, Any] | None,
        token_cache: _FakeCache,
        change_state: bool = False,
    ) -> None:
        self._accounts = accounts
        self._silent_result = silent_result
        self.token_cache = token_cache
        self._change_state = change_state
        self.silent_calls: list[Any] = []

    def get_accounts(self) -> list[Any]:
        return self._accounts

    def acquire_token_silent(
        self, scopes: list[str], account: Any, **kwargs: Any
    ) -> dict[str, Any] | None:
        self.silent_calls.append((scopes, account, kwargs))
        if self._change_state:
            self.token_cache.has_state_changed = True
            self.token_cache._state = '{"cached": "refreshed"}'
        return self._silent_result


def _install_fake_msal(
    monkeypatch: pytest.MonkeyPatch,
    *,
    accounts: list[Any],
    silent_result: dict[str, Any] | None,
    change_state: bool = False,
) -> dict[str, Any]:
    """Install a fake ``msal`` module and return a recorder dict."""
    recorder: dict[str, Any] = {}
    cache = _FakeCache()

    class _FakeMsal:
        SerializableTokenCache = _FakeCache

        @staticmethod
        def PublicClientApplication(
            *, client_id: str, authority: str, token_cache: _FakeCache
        ) -> _FakeApp:
            recorder["client_id"] = client_id
            recorder["authority"] = authority
            app = _FakeApp(
                accounts=accounts,
                silent_result=silent_result,
                token_cache=token_cache,
                change_state=change_state,
            )
            recorder["app"] = app
            return app

    fake = _FakeMsal()
    monkeypatch.setitem(sys.modules, "msal", fake)
    recorder["cache"] = cache
    return recorder


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def test_build_token_provider_returns_none_when_unset(tmp_path: Path) -> None:
    """No oauth2_provider → dispatcher returns None (fall back to password)."""
    cfg = _make_config(tmp_path)
    assert build_token_provider(cfg) is None


def test_build_token_provider_returns_none_for_other_provider(
    tmp_path: Path,
) -> None:
    """A non-microsoft provider → dispatcher returns None."""
    cfg = _make_config(tmp_path, oauth2_provider="google")
    assert build_token_provider(cfg) is None


def test_build_token_provider_returns_callable_for_microsoft(
    tmp_path: Path,
) -> None:
    """oauth2_provider='microsoft' → dispatcher returns a callable."""
    cfg = _make_config(tmp_path, oauth2_provider="microsoft")
    provider = build_token_provider(cfg)
    assert callable(provider)


# ---------------------------------------------------------------------------
# Cache-path derivation
# ---------------------------------------------------------------------------


def test_cache_path_derives_from_db_path(tmp_path: Path) -> None:
    """Cache lives at <dirname(db_path)>/msal_cache.json."""
    cfg = _make_config(tmp_path, db_path=".data/contoso/mail.db")
    assert cache_path_for(cfg) == Path(".data/contoso/msal_cache.json")


# ---------------------------------------------------------------------------
# Silent-token happy path
# ---------------------------------------------------------------------------


def test_provider_returns_silent_access_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-seeded cache yields a fresh token via acquire_token_silent."""
    recorder = _install_fake_msal(
        monkeypatch,
        accounts=[{"username": "user@contoso.com"}],
        silent_result={"access_token": "fresh-token-123"},
    )
    cfg = _make_config(tmp_path, oauth2_provider="microsoft")
    provider = build_token_provider(cfg)
    assert provider is not None

    token = provider()
    assert token == "fresh-token-123"
    # Only the two resource scopes are passed (MSAL adds offline_access).
    scopes, _account, _kwargs = recorder["app"].silent_calls[0]
    assert scopes == [MICROSOFT_IMAP_SCOPE, MICROSOFT_SMTP_SCOPE]


# ---------------------------------------------------------------------------
# Cache persistence after silent refresh
# ---------------------------------------------------------------------------


def test_provider_persists_cache_after_state_changing_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When acquire_token_silent changes cache state, the updated cache
    is written to cache_path_for(cfg) on disk."""
    _install_fake_msal(
        monkeypatch,
        accounts=[{"username": "user@contoso.com"}],
        silent_result={"access_token": "refreshed-token-456"},
        change_state=True,
    )
    cfg = _make_config(tmp_path, oauth2_provider="microsoft")
    provider = build_token_provider(cfg)
    assert provider is not None

    token = provider()
    assert token == "refreshed-token-456"

    # The cache file must exist and contain the refreshed serialized state.
    cache_path = cache_path_for(cfg)
    assert cache_path.exists()
    assert cache_path.read_text() == '{"cached": "refreshed"}'


def test_persisted_cache_has_restrictive_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The persisted cache file is created with mode 0o600 inside a 0o700
    directory, so the refresh token is not readable by other local users."""
    _install_fake_msal(
        monkeypatch,
        accounts=[{"username": "user@contoso.com"}],
        silent_result={"access_token": "fresh-token-123"},
        change_state=True,
    )
    cfg = _make_config(tmp_path, oauth2_provider="microsoft")
    provider = build_token_provider(cfg)
    assert provider is not None
    provider()

    cache_path = cache_path_for(cfg)
    assert cache_path.exists()

    # File mode: 0o600 (owner rw- only).
    import stat

    file_mode = stat.S_IMODE(cache_path.stat().st_mode)
    assert file_mode == 0o600, f"expected 0o600, got {file_mode:#o}"

    # Directory mode: 0o700 (owner rwx only).
    dir_mode = stat.S_IMODE(cache_path.parent.stat().st_mode)
    assert dir_mode == 0o700, f"expected 0o700, got {dir_mode:#o}"


def test_provider_does_not_persist_cache_when_state_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the cache state is unchanged, _persist_cache is a no-op —
    no file is written."""
    _install_fake_msal(
        monkeypatch,
        accounts=[{"username": "user@contoso.com"}],
        silent_result={"access_token": "fresh-token-123"},
        change_state=False,
    )
    cfg = _make_config(tmp_path, oauth2_provider="microsoft")
    provider = build_token_provider(cfg)
    assert provider is not None

    token = provider()
    assert token == "fresh-token-123"

    # has_state_changed was False → cache file must NOT have been created.
    cache_path = cache_path_for(cfg)
    assert not cache_path.exists()


# ---------------------------------------------------------------------------
# Missing / expired cache
# ---------------------------------------------------------------------------


def test_provider_raises_when_no_cached_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No cached account → actionable error telling the user to log in."""
    _install_fake_msal(monkeypatch, accounts=[], silent_result=None)
    cfg = _make_config(tmp_path, oauth2_provider="microsoft")
    provider = build_token_provider(cfg)
    assert provider is not None

    with pytest.raises(ConfigurationError) as exc:
        provider()
    assert "auth login" in str(exc.value)


def test_provider_raises_when_silent_acquisition_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Account present but refresh fails → actionable login error."""
    _install_fake_msal(
        monkeypatch,
        accounts=[{"username": "user@contoso.com"}],
        silent_result=None,
    )
    cfg = _make_config(tmp_path, oauth2_provider="microsoft")
    provider = build_token_provider(cfg)
    assert provider is not None

    with pytest.raises(ConfigurationError) as exc:
        provider()
    assert "auth login" in str(exc.value)


# ---------------------------------------------------------------------------
# parse_xoauth2_error
# ---------------------------------------------------------------------------


def test_parse_xoauth2_error_valid_json() -> None:
    """Valid JSON challenge → parsed dict."""
    challenge = b'{"status":"400","schemes":"Bearer"}'
    result = parse_xoauth2_error(challenge)
    assert result == {"status": "400", "schemes": "Bearer"}


def test_parse_xoauth2_error_empty_bytes() -> None:
    """Empty challenge → empty dict."""
    assert parse_xoauth2_error(b"") == {}


def test_parse_xoauth2_error_invalid_json() -> None:
    """Malformed JSON → empty dict (no raise)."""
    assert parse_xoauth2_error(b"not json") == {}


def test_parse_xoauth2_error_non_dict_json() -> None:
    """JSON array/string/number → empty dict."""
    assert parse_xoauth2_error(b"[1,2,3]") == {}


# ---------------------------------------------------------------------------
# extract_cae_claims
# ---------------------------------------------------------------------------


def test_extract_cae_claims_with_claims() -> None:
    """Schemes field containing claims=\"XYZ\" → XYZ."""
    error_info = {"schemes": 'Bearer claims="eyJhY2Nlc3NfdG9rZW4iOnsibmJmIjp7fX19"'}
    result = extract_cae_claims(error_info)
    assert result == "eyJhY2Nlc3NfdG9rZW4iOnsibmJmIjp7fX19"


def test_extract_cae_claims_without_claims() -> None:
    """Schemes field without claims= → None."""
    error_info = {"schemes": 'Bearer authorization_uri="https://..."'}
    result = extract_cae_claims(error_info)
    assert result is None


def test_extract_cae_claims_empty_schemes() -> None:
    """Empty or missing schemes → None."""
    assert extract_cae_claims({}) is None
    assert extract_cae_claims({"schemes": ""}) is None


# ---------------------------------------------------------------------------
# acquire_fresh_token
# ---------------------------------------------------------------------------


def test_acquire_fresh_token_returns_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force-refresh with a valid cached account → returns fresh token."""
    _install_fake_msal(
        monkeypatch,
        accounts=[{"username": "user@contoso.com"}],
        silent_result={"access_token": "fresh-token-789"},
    )
    cfg = _make_config(tmp_path, oauth2_provider="microsoft")
    token = acquire_fresh_token(cfg)
    assert token == "fresh-token-789"


def test_acquire_fresh_token_passes_claims_challenge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """claims_challenge is forwarded to acquire_token_silent."""
    recorder = _install_fake_msal(
        monkeypatch,
        accounts=[{"username": "user@contoso.com"}],
        silent_result={"access_token": "fresh-cae-token"},
    )
    cfg = _make_config(tmp_path, oauth2_provider="microsoft")
    token = acquire_fresh_token(cfg, claims_challenge="CAE_CLAIMS_XYZ")
    assert token == "fresh-cae-token"
    # Verify the app was constructed (the config was used)
    assert "app" in recorder


def test_acquire_fresh_token_no_accounts_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No cached accounts → ConfigurationError with login hint."""
    _install_fake_msal(monkeypatch, accounts=[], silent_result=None)
    cfg = _make_config(tmp_path, oauth2_provider="microsoft")
    with pytest.raises(ConfigurationError) as exc:
        acquire_fresh_token(cfg)
    assert "auth login" in str(exc.value)


def test_acquire_fresh_token_silent_fails_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """acquire_token_silent returns None → ConfigurationError."""
    _install_fake_msal(
        monkeypatch,
        accounts=[{"username": "user@contoso.com"}],
        silent_result=None,
    )
    cfg = _make_config(tmp_path, oauth2_provider="microsoft")
    with pytest.raises(ConfigurationError) as exc:
        acquire_fresh_token(cfg)
    assert "auth login" in str(exc.value)


def test_acquire_fresh_token_persists_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After force-refresh, the cache file is written to disk."""
    _install_fake_msal(
        monkeypatch,
        accounts=[{"username": "user@contoso.com"}],
        silent_result={"access_token": "fresh-cached-token"},
        change_state=True,
    )
    cfg = _make_config(tmp_path, oauth2_provider="microsoft")
    token = acquire_fresh_token(cfg)
    assert token == "fresh-cached-token"
    cache_path = cache_path_for(cfg)
    assert cache_path.exists()
    assert cache_path.read_text() == '{"cached": "refreshed"}'


# ---------------------------------------------------------------------------
# classify_xoauth2_auth_error
# ---------------------------------------------------------------------------


def test_classify_conditional_access_aadsts_53003() -> None:
    """Challenge with AADSTS53003 → message mentions Conditional Access."""
    msg = classify_xoauth2_auth_error(
        b'{"error_description":"AADSTS53003: Blocked by Conditional Access"}',
        username="user@contoso.com",
        host="outlook.office365.com",
        port=993,
    )
    assert "Conditional Access" in msg
    assert "user@contoso.com" in msg
    assert "outlook.office365.com" in msg


def test_classify_conditional_access_aadsts_53000() -> None:
    """AADSTS53000 (device not compliant) also classified as CA."""
    msg = classify_xoauth2_auth_error(
        b'{"error":"AADSTS53000: Device not compliant"}',
        username="u@x.com",
        host="h",
        port=1,
    )
    assert "Conditional Access" in msg


def test_classify_generic_error() -> None:
    """Challenge without known AADSTS code → generic message."""
    msg = classify_xoauth2_auth_error(
        b'{"status":"400","schemes":"Bearer"}',
        username="u@x.com",
        host="h",
        port=1,
    )
    # The generic message does NOT contain the specific "Conditional
    # Access policy blocked" phrase (it only mentions CA in a
    # follow-up suggestion).
    assert "Conditional Access policy blocked" not in msg
    assert "Token refresh was attempted" in msg
    assert "auth login" in msg
