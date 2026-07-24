"""Tests for the detect_provider integration (LLM agent orchestration)."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.config.detect import (
    DetectedProvider,
    DetectionError,
    MailProvider,
    detect_provider,
)
from robotsix_auto_mail.config.schema import ConfigurationError


# ---------------------------------------------------------------------------
# detect_provider — integration-style tests
# ---------------------------------------------------------------------------


def test_detect_provider_success() -> None:
    """Mock the provider; detect_provider returns expected MailProvider."""
    mock_run_result = mock.MagicMock()
    mock_run_result.output = DetectedProvider(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        imap_port=993,
        imap_tls_mode="direct-tls",
        smtp_port=587,
        smtp_tls_mode="starttls",
    )
    mock_handle = mock.MagicMock()
    mock_handle.run_sync.return_value = mock_run_result

    mock_provider = mock.MagicMock()
    mock_provider.build_agent.return_value = mock_handle
    mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

    with mock.patch(
        "robotsix_llmio.core.factory.get_provider_for_identifier",
        return_value=mock_provider,
    ):
        result = detect_provider("user@example.com", api_key="sk-test")

    assert isinstance(result, MailProvider)
    assert result.imap_host == "imap.example.com"
    assert result.smtp_host == "smtp.example.com"
    assert result.imap_port == 993
    assert result.imap_tls_mode == "direct-tls"
    assert result.smtp_port == 587
    assert result.smtp_tls_mode == "starttls"
    mock_handle.close.assert_called_once()


def test_detect_provider_passes_api_key_arg() -> None:
    """When api_key is passed as argument, it's used instead of env var."""
    mock_run_result = mock.MagicMock()
    mock_run_result.output = DetectedProvider(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
    )
    mock_handle = mock.MagicMock()
    mock_handle.run_sync.return_value = mock_run_result

    mock_provider = mock.MagicMock()
    mock_provider.build_agent.return_value = mock_handle
    mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

    mock_provider_cls = mock.MagicMock(return_value=mock_provider)

    with mock.patch(
        "robotsix_llmio.core.factory.get_provider_for_identifier",
        mock_provider_cls,
    ):
        result = detect_provider("user@example.com", api_key="sk-arg-key")

    assert result.imap_host == "imap.example.com"
    mock_provider_cls.assert_called_once()
    assert mock_provider_cls.call_args.kwargs["api_key"] == "sk-arg-key"


def test_detect_provider_llm_call_error() -> None:
    """When call_with_retry raises, DetectionError wraps the original message."""
    mock_handle = mock.MagicMock()
    mock_provider = mock.MagicMock()
    mock_provider.build_agent.return_value = mock_handle
    mock_handle.run_sync.side_effect = RuntimeError("LLM API timeout")

    with mock.patch(
        "robotsix_llmio.core.factory.get_provider_for_identifier",
        return_value=mock_provider,
    ):
        with pytest.raises(DetectionError) as exc:
            detect_provider("user@example.com", api_key="sk-test")

    assert "LLM API timeout" in str(exc.value)
    mock_handle.close.assert_called_once()


def test_detect_provider_missing_api_key() -> None:
    """No api_key arg and no LLM_API_KEY env var → DetectionError."""
    with mock.patch(
        "robotsix_auto_mail.core._llm_agent.resolve_llm_api_key",
        side_effect=ConfigurationError(
            "No LLM API key found — add llm_api_key to config/config.json"
        ),
    ):
        with pytest.raises(DetectionError) as exc:
            detect_provider("user@example.com")
    assert "llm_api_key" in str(exc.value)


def test_detect_provider_level_default() -> None:
    """When no tier arg is passed, build_agent is called with level=1 (cheap)."""
    mock_run_result = mock.MagicMock()
    mock_run_result.output = DetectedProvider(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
    )
    mock_handle = mock.MagicMock()
    mock_handle.run_sync.return_value = mock_run_result

    mock_provider = mock.MagicMock()
    mock_provider.build_agent.return_value = mock_handle
    mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

    with mock.patch(
        "robotsix_llmio.core.factory.get_provider_for_identifier",
        return_value=mock_provider,
    ):
        detect_provider("user@example.com", api_key="sk-test")

    mock_provider.build_agent.assert_called_once()
    call_kwargs = mock_provider.build_agent.call_args.kwargs
    assert call_kwargs["level"] == 1


def test_detect_provider_explicit_level() -> None:
    """When level=2 is passed, build_agent is called with level=2."""
    mock_run_result = mock.MagicMock()
    mock_run_result.output = DetectedProvider(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
    )
    mock_handle = mock.MagicMock()
    mock_handle.run_sync.return_value = mock_run_result

    mock_provider = mock.MagicMock()
    mock_provider.build_agent.return_value = mock_handle
    mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

    with mock.patch(
        "robotsix_llmio.core.factory.get_provider_for_identifier",
        return_value=mock_provider,
    ):
        detect_provider("user@example.com", api_key="sk-test", level=2)

    mock_provider.build_agent.assert_called_once()
    call_kwargs = mock_provider.build_agent.call_args.kwargs
    assert call_kwargs["level"] == 2
