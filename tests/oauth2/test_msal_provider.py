"""Tests for the MSAL-backed OAuth2 token provider.

``msal`` is an optional dependency and is NOT installed in the test
environment, so these tests inject a fake ``msal`` module into
``sys.modules`` to exercise the provider logic without the real library.
"""

from __future__ import annotations

import json
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
        self.silent_calls.append({"scopes": scopes, "account": account, **kwargs})
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
    call = recorder["app"].silent_calls[0]
    assert call["scopes"] == [MICROSOFT_IMAP_SCOPE, MICROSOFT_SMTP_SCOPE]


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


def test_parse_xoauth2_error_valid() -> None:
    """Valid JSON challenge parses correctly."""
    result = parse_xoauth2_error(
        json.dumps({"status": "400", "error_codes": [53003]}).encode()
    )
    assert result == {"status": "400", "error_codes": [53003]}


def test_parse_xoauth2_error_nul_stripped() -> None:
    """Trailing NUL byte is stripped before parsing."""
    result = parse_xoauth2_error(
        json.dumps({"status": "400", "error_codes": [53003]}).encode() + b"\x00"
    )
    assert result == {"status": "400", "error_codes": [53003]}


def test_parse_xoauth2_error_garbage() -> None:
    """Non-JSON input returns empty dict."""
    result = parse_xoauth2_error(b"not-json")
    assert result == {}


# ---------------------------------------------------------------------------
# extract_cae_claims
# ---------------------------------------------------------------------------


def test_extract_cae_claims_present() -> None:
    """Claims parameter is extracted from the schemes field."""
    claims = extract_cae_claims(
        {
            "schemes": 'Bearer error="insufficient_claims", '
            'claims="eyJhY2Nlc3NUb2tlbiI6IjEifQ=="'
        }
    )
    assert claims == "eyJhY2Nlc3NUb2tlbiI6IjEifQ=="


def test_extract_cae_claims_absent() -> None:
    """No claims parameter → None."""
    assert extract_cae_claims({"schemes": "Bearer"}) is None


def test_extract_cae_claims_empty() -> None:
    """Empty dict → None."""
    assert extract_cae_claims({}) is None


# ---------------------------------------------------------------------------
# acquire_fresh_token
# ---------------------------------------------------------------------------


def test_acquire_fresh_token_passes_force_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """acquire_fresh_token calls acquire_token_silent with force_refresh=True."""
    recorder = _install_fake_msal(
        monkeypatch,
        accounts=[{"username": "user@contoso.com"}],
        silent_result={"access_token": "fresh"},
    )
    cfg = _make_config(tmp_path, oauth2_provider="microsoft")
    token = acquire_fresh_token(cfg)
    assert token == "fresh"
    assert recorder["app"].silent_calls[0]["force_refresh"] is True


def test_acquire_fresh_token_passes_claims_challenge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """acquire_fresh_token passes claims_challenge to acquire_token_silent."""
    recorder = _install_fake_msal(
        monkeypatch,
        accounts=[{"username": "user@contoso.com"}],
        silent_result={"access_token": "fresh"},
    )
    cfg = _make_config(tmp_path, oauth2_provider="microsoft")
    token = acquire_fresh_token(cfg, claims_challenge="CLAIM")
    assert token == "fresh"
    assert recorder["app"].silent_calls[0]["claims_challenge"] == "CLAIM"


def test_acquire_fresh_token_no_accounts_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No cached accounts → ConfigurationError."""
    _install_fake_msal(monkeypatch, accounts=[], silent_result=None)
    cfg = _make_config(tmp_path, oauth2_provider="microsoft")
    with pytest.raises(ConfigurationError) as exc:
        acquire_fresh_token(cfg)
    assert "auth login" in str(exc.value)


def test_acquire_fresh_token_no_token_in_result_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Result missing access_token → ConfigurationError."""
    _install_fake_msal(
        monkeypatch,
        accounts=[{"username": "user@contoso.com"}],
        silent_result={"error": "bad"},
    )
    cfg = _make_config(tmp_path, oauth2_provider="microsoft")
    with pytest.raises(ConfigurationError) as exc:
        acquire_fresh_token(cfg)
    assert "auth login" in str(exc.value)


# ---------------------------------------------------------------------------
# classify_xoauth2_auth_error
# ---------------------------------------------------------------------------


def _challenge(error_codes: list[int]) -> bytes:
    """Helper: produce a JSON challenge with the given error codes."""
    return json.dumps({"error_codes": error_codes}).encode()


def test_classify_conditional_access_code() -> None:
    """AADSTS 53003 → message mentions Conditional Access and the code."""
    msg = classify_xoauth2_auth_error(
        _challenge([53003]),
        username="u@c.com",
        host="outlook.office365.com",
        port=993,
    )
    assert "Conditional Access" in msg
    assert "AADSTS53003" in msg


def test_classify_unknown_code() -> None:
    """Non-CA code → message says 'after force-refresh' but not 'Conditional Access'."""
    msg = classify_xoauth2_auth_error(
        _challenge([99999]),
        username="u@c.com",
        host="outlook.office365.com",
        port=993,
    )
    assert "after force-refresh" in msg
    assert "Conditional Access" not in msg


def test_classify_no_codes() -> None:
    """Empty challenge → message says 'after force-refresh'."""
    msg = classify_xoauth2_auth_error(
        b"{}",
        username="u@c.com",
        host="outlook.office365.com",
        port=993,
    )
    assert "after force-refresh" in msg
