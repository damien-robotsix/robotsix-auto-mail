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
    build_token_provider,
    cache_path_for,
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
    ) -> None:
        self._accounts = accounts
        self._silent_result = silent_result
        self.token_cache = token_cache
        self.silent_calls: list[Any] = []

    def get_accounts(self) -> list[Any]:
        return self._accounts

    def acquire_token_silent(
        self, scopes: list[str], account: Any
    ) -> dict[str, Any] | None:
        self.silent_calls.append((scopes, account))
        return self._silent_result


def _install_fake_msal(
    monkeypatch: pytest.MonkeyPatch,
    *,
    accounts: list[Any],
    silent_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Install a fake ``msal`` module and return a recorder dict."""
    recorder: dict[str, Any] = {}
    cache = _FakeCache()

    class _FakeMsal:
        SerializableTokenCache = _FakeCache

        @staticmethod
        def PublicClientApplication(  # noqa: N802
            *, client_id: str, authority: str, token_cache: _FakeCache
        ) -> _FakeApp:
            recorder["client_id"] = client_id
            recorder["authority"] = authority
            app = _FakeApp(
                accounts=accounts,
                silent_result=silent_result,
                token_cache=token_cache,
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
    scopes, _account = recorder["app"].silent_calls[0]
    assert scopes == [MICROSOFT_IMAP_SCOPE, MICROSOFT_SMTP_SCOPE]


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
