"""LLM-based host refinement tests (_refine_with_llm)."""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from robotsix_auto_mail.cli.config import _refine_with_llm, _VerifyResult
from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.config.detect import DetectionError, MailProvider
from tests.cli.conftest import _build_config


def test_refine_with_llm_success(capsys: pytest.CaptureFixture[str]) -> None:
    """LLM returns a refined provider → outcome includes new config + provider."""
    provider = MailProvider(imap_host="imap.bad.com", smtp_host="smtp.bad.com")
    config = MailConfig(
        username="user@example.com",
        imap_host="imap.bad.com",
        smtp_host="smtp.bad.com",
        password="pw",  # pragma: allowlist secret
    )
    result = _VerifyResult(imap_ok=False, smtp_ok=True, imap_error="refused")

    def _detect(email: str, **kwargs: Any) -> MailProvider:
        return MailProvider(imap_host="imap.good.com", smtp_host="smtp.good.com")

    outcome = _refine_with_llm(
        _build_config,
        provider,
        config,
        result,
        email="user@example.com",
        api_key="sk-test",  # pragma: allowlist secret
        llm_provider_model=None,
        mx_hosts=["mx1.example.com"],
        detect_provider=_detect,
        _detection_error=DetectionError,
    )
    assert outcome.config is not None
    assert outcome.config.imap_host == "imap.good.com"
    assert outcome.config.smtp_host == "smtp.good.com"
    assert (
        outcome.config.password.get_secret_value() == "pw"  # pragma: allowlist secret
    )  # preserved from original  # pragma: allowlist secret
    assert outcome.provider is not None
    assert outcome.provider.imap_host == "imap.good.com"


def test_refine_with_llm_error(capsys: pytest.CaptureFixture[str]) -> None:
    """LLM raises DetectionError → no config/provider."""
    provider = MailProvider(imap_host="imap.bad.com", smtp_host="smtp.bad.com")
    config = MailConfig(
        username="user@example.com",
        imap_host="imap.bad.com",
        smtp_host="smtp.bad.com",
        password="pw",  # pragma: allowlist secret
    )
    result = _VerifyResult(imap_ok=False, smtp_ok=True, imap_error="refused")

    def _detect_error(email: str, **kwargs: Any) -> MailProvider:
        raise DetectionError("no can do")

    outcome = _refine_with_llm(
        _build_config,
        provider,
        config,
        result,
        email="user@example.com",
        api_key="sk-test",  # pragma: allowlist secret
        llm_provider_model=None,
        mx_hosts=[],
        detect_provider=_detect_error,
        _detection_error=DetectionError,
    )
    assert outcome.config is None
    assert outcome.provider is None
    captured = capsys.readouterr()
    assert "LLM refinement error: no can do" in captured.err


def test_refine_with_llm_returns_none(capsys: pytest.CaptureFixture[str]) -> None:
    """LLM returns None provider → no config/provider."""
    provider = MailProvider(imap_host="imap.bad.com", smtp_host="smtp.bad.com")
    config = MailConfig(
        username="user@example.com",
        imap_host="imap.bad.com",
        smtp_host="smtp.bad.com",
        password="pw",  # pragma: allowlist secret
    )
    result = _VerifyResult(imap_ok=False, smtp_ok=True, imap_error="refused")

    def _detect_none(email: str, **kwargs: Any) -> MailProvider | None:
        return None

    outcome = _refine_with_llm(
        _build_config,
        provider,
        config,
        result,
        email="user@example.com",
        api_key="sk-test",  # pragma: allowlist secret
        llm_provider_model=None,
        mx_hosts=[],
        detect_provider=_detect_none,
        _detection_error=DetectionError,
    )
    assert outcome.config is None
    assert outcome.provider is None
