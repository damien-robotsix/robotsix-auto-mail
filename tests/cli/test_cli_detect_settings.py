"""Tests for CLI detect settings display and LLM host refinement on connection failure."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.cli import main
from robotsix_auto_mail.cli.config import _detect_settings
from robotsix_auto_mail.config.detect import DetectionError, MailProvider

from tests.cli.conftest import (
    _ok_result,
    _host_fail_result,
    _mock_autoconfig,
    _mock_autoconfig_none,
    _mock_mx,
    _mock_mx_empty,
    _mock_provider_from_mx,
    _mock_detect,
    _mock_detect_error,
)


def test_detect_refines_host_with_llm_on_connection_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """A connection failure triggers an LLM refinement that then succeeds."""
    output = tmp_path / "cfg.yaml"
    bad = MailProvider(imap_host="imap.bad.net", smtp_host="smtp.gmail.com")
    good = MailProvider(imap_host="imap.good.net", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            side_effect=[bad, good],
        ) as mock_dp,
        mock.patch(
            "robotsix_auto_mail.cli._verify_config",
            side_effect=[_host_fail_result(), _ok_result()],
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
            ]
        )

    assert rc == 0
    # initial guess + one refinement
    assert mock_dp.call_count == 2
    # the refinement was given failure feedback
    assert mock_dp.call_args.kwargs.get("feedback")
    assert "imap.good.net" in output.read_text()
    assert "Refining" in capsys.readouterr().err


def test_detect_prompts_for_host_when_llm_cannot_fix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """When LLM refinement errors, detect prompts for the host, then verifies."""
    output = tmp_path / "cfg.yaml"
    bad = MailProvider(imap_host="imap.bad.net", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            side_effect=[bad, DetectionError("llm down")],
        ),
        mock.patch(
            "robotsix_auto_mail.cli._verify_config",
            side_effect=[_host_fail_result(), _ok_result()],
        ),
        mock.patch("builtins.input", return_value="mail.manual.net") as mock_input,
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
            ]
        )

    assert rc == 0
    mock_input.assert_called()
    assert "mail.manual.net" in output.read_text()
    assert "manually" in capsys.readouterr().err


def test_detect_settings_autoconfig_hit(capsys: pytest.CaptureFixture[str]) -> None:
    """Step 1 autoconfig succeeds → provider returned, no MX/LLM lookup."""
    provider, _mx_hosts = _detect_settings(
        email="user@example.com",
        api_key=None,
        llm_provider_model=None,
        autoconfig_lookup=_mock_autoconfig,
        mx_lookup=_mock_mx,
        provider_from_mx=_mock_provider_from_mx,
        detect_provider=_mock_detect,
        _detection_error=DetectionError,
    )
    assert provider is not None
    assert provider.imap_host == "imap.autoconfig.com"
    assert provider.smtp_host == "smtp.autoconfig.com"
    captured = capsys.readouterr()
    assert "autoconfig: imap=" in captured.err


def test_detect_settings_mx_hit(capsys: pytest.CaptureFixture[str]) -> None:
    """Autoconfig miss → MX lookup → provider_from_mx hit."""
    provider, mx_hosts = _detect_settings(
        email="user@example.com",
        api_key=None,
        llm_provider_model=None,
        autoconfig_lookup=_mock_autoconfig_none,
        mx_lookup=_mock_mx,
        provider_from_mx=_mock_provider_from_mx,
        detect_provider=_mock_detect,
        _detection_error=DetectionError,
    )
    assert provider is not None
    assert provider.imap_host == "imap.mx.com"
    assert mx_hosts == ["mx1.example.com", "mx2.example.com"]
    captured = capsys.readouterr()
    assert "MX:" in captured.err
    assert "MX provider:" in captured.err


def test_detect_settings_mx_empty_no_provider(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Autoconfig miss, no MX records → falls through to LLM."""
    provider, _mx_hosts = _detect_settings(
        email="user@example.com",
        api_key="sk-test",  # pragma: allowlist secret
        llm_provider_model=None,
        autoconfig_lookup=_mock_autoconfig_none,
        mx_lookup=_mock_mx_empty,
        provider_from_mx=_mock_provider_from_mx,
        detect_provider=_mock_detect,
        _detection_error=DetectionError,
    )
    assert provider is not None
    assert provider.imap_host == "imap.llm.com"
    captured = capsys.readouterr()
    assert "no known provider — asking the LLM" in captured.err


def test_detect_settings_llm_hit(capsys: pytest.CaptureFixture[str]) -> None:
    """Autoconfig + MX both miss → LLM provides the answer."""

    def _mx_lookup(email: str) -> list[str]:
        return ["mx.unknown.com"]

    def _provider_from_mx(mx_hosts: list[str]) -> MailProvider | None:
        return None  # unknown MX host

    provider, _mx_hosts = _detect_settings(
        email="user@example.com",
        api_key="sk-test",  # pragma: allowlist secret
        llm_provider_model=None,
        autoconfig_lookup=_mock_autoconfig_none,
        mx_lookup=_mx_lookup,
        provider_from_mx=_provider_from_mx,
        detect_provider=_mock_detect,
        _detection_error=DetectionError,
    )
    assert provider is not None
    assert provider.imap_host == "imap.llm.com"
    captured = capsys.readouterr()
    assert "no known provider — asking the LLM" in captured.err
    assert "LLM: imap=" in captured.err


def test_detect_settings_all_miss(capsys: pytest.CaptureFixture[str]) -> None:
    """Autoconfig miss, MX miss, LLM raises DetectionError → (None, mx_hosts)."""
    provider, _mx_hosts = _detect_settings(
        email="user@example.com",
        api_key="sk-test",  # pragma: allowlist secret
        llm_provider_model=None,
        autoconfig_lookup=_mock_autoconfig_none,
        mx_lookup=_mock_mx_empty,
        provider_from_mx=_mock_provider_from_mx,
        detect_provider=_mock_detect_error,
        _detection_error=DetectionError,
    )
    assert provider is None
    captured = capsys.readouterr()
    assert "Error: LLM unavailable" in captured.err
