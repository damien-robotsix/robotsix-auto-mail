"""Shared fixtures and helpers for CLI detect tests."""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from robotsix_auto_mail.cli.config import _VerifyResult
from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.config.detect import DetectionError, MailProvider


@pytest.fixture
def no_autoconfig() -> object:
    """Force autoconfig + MX detection to miss so tests reach the LLM path."""
    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.autoconfig_lookup", return_value=None
        ),
        mock.patch("robotsix_auto_mail.config.detect.mx_lookup", return_value=[]),
        mock.patch(
            "robotsix_auto_mail.config.detect.provider_from_mx", return_value=None
        ),
    ):
        yield


def _ok_result() -> object:
    from robotsix_auto_mail.cli import _VerifyResult

    return _VerifyResult(imap_ok=True, smtp_ok=True)


def _auth_fail_result() -> object:
    from robotsix_auto_mail.cli import _VerifyResult

    return _VerifyResult(
        imap_ok=False,
        smtp_ok=False,
        imap_auth=True,
        smtp_auth=True,
        imap_error="auth",
        smtp_error="auth",
    )


def _host_fail_result() -> object:
    """IMAP host unreachable, SMTP ok — a connection (not auth) failure."""
    from robotsix_auto_mail.cli import _VerifyResult

    return _VerifyResult(
        imap_ok=False,
        smtp_ok=True,
        imap_error="connection refused",
    )


def _mock_autoconfig(email: str) -> MailProvider | None:
    return MailProvider(
        imap_host="imap.autoconfig.com", smtp_host="smtp.autoconfig.com"
    )


def _mock_autoconfig_none(email: str) -> MailProvider | None:
    return None


def _mock_mx(email: str) -> list[str]:
    return ["mx1.example.com", "mx2.example.com"]


def _mock_mx_empty(email: str) -> list[str]:
    return []


def _mock_provider_from_mx(mx_hosts: list[str]) -> MailProvider | None:
    if mx_hosts:
        return MailProvider(imap_host="imap.mx.com", smtp_host="smtp.mx.com")
    return None


def _mock_detect(email: str, **kwargs: Any) -> MailProvider:
    return MailProvider(imap_host="imap.llm.com", smtp_host="smtp.llm.com")


def _mock_detect_error(email: str, **kwargs: Any) -> MailProvider:
    raise DetectionError("LLM unavailable")


# ---------------------------------------------------------------------------
# Shared refine helpers
# ---------------------------------------------------------------------------


def _accounts(cfg: MailConfig, account_id: str = "default") -> MailAccountsConfig:
    """Wrap a single ``MailConfig`` in a one-element accounts container."""
    return MailAccountsConfig(
        accounts=(MailAccount(account_id=account_id, config=cfg, label=None),),
        default_account_id=account_id,
    )


def _build_config(provider: MailProvider, password: str | None) -> MailConfig:
    return MailConfig(
        username="user@example.com",
        imap_host=provider.imap_host,
        smtp_host=provider.smtp_host,
        password=password or "",
    )


def _provider_to_config(
    provider: MailProvider, email: str, password: str = ""
) -> MailConfig:
    return MailConfig(
        username=email,
        imap_host=provider.imap_host,
        smtp_host=provider.smtp_host,
        password=password,
    )


def _refine_test_config() -> MailConfig:
    """Build a minimal MailConfig for refinement-helper unit tests."""
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
    )


def _refine_host_result() -> _VerifyResult:
    """Build an IMAP-host-failure _VerifyResult for helper unit tests."""
    return _VerifyResult(imap_ok=False, smtp_ok=True, imap_error="connection refused")
