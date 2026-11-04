"""Tests for CLI detect settings display and manual host refinement on failure."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.cli import main
from robotsix_auto_mail.cli.config import _detect_settings
from robotsix_auto_mail.detect import MailProvider
from tests.cli.conftest import (
    _host_fail_result,
    _mock_autoconfig,
    _mock_autoconfig_none,
    _mock_mx,
    _mock_mx_empty,
    _mock_provider_from_mx,
    _ok_result,
)


def test_detect_prompts_for_host_on_connection_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A connection failure prompts for the host manually, then verifies."""
    bad = MailProvider(imap_host="imap.bad.net", smtp_host="smtp.gmail.com")

    with (
        mock.patch("robotsix_auto_mail.detect.autoconfig_lookup", return_value=bad),
        mock.patch(
            "robotsix_auto_mail.cli._verify_config",
            side_effect=[_host_fail_result(), _ok_result()],
        ),
        mock.patch("builtins.input", return_value="mail.manual.net") as mock_input,
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--password",
                "pw",
            ]
        )

    assert rc == 0
    mock_input.assert_called()
    captured = capsys.readouterr()
    assert "mail.manual.net" in captured.out
    assert "manually" in captured.err


def test_detect_exits_when_autoconfig_and_mx_miss(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect exits 1 with a manual-configuration error when nothing resolves."""
    rc = main(["detect", "user@unknown.example", "--password", "pw"])

    assert rc == 1
    captured = capsys.readouterr()
    assert "could not auto-detect settings" in captured.err
    assert "manually" in captured.err


def test_detect_settings_autoconfig_hit(capsys: pytest.CaptureFixture[str]) -> None:
    """Step 1 autoconfig succeeds → provider returned, no MX lookup."""
    mx_lookup = mock.MagicMock(return_value=[])
    provider = _detect_settings(
        email="user@example.com",
        autoconfig_lookup=_mock_autoconfig,
        mx_lookup=mx_lookup,
        provider_from_mx=_mock_provider_from_mx,
    )
    assert provider is not None
    assert provider.imap_host == "imap.autoconfig.com"
    assert provider.smtp_host == "smtp.autoconfig.com"
    mx_lookup.assert_not_called()
    captured = capsys.readouterr()
    assert "autoconfig: imap=" in captured.err


def test_detect_settings_mx_hit(capsys: pytest.CaptureFixture[str]) -> None:
    """Autoconfig miss → MX lookup → provider_from_mx hit."""
    provider = _detect_settings(
        email="user@example.com",
        autoconfig_lookup=_mock_autoconfig_none,
        mx_lookup=_mock_mx,
        provider_from_mx=_mock_provider_from_mx,
    )
    assert provider is not None
    assert provider.imap_host == "imap.mx.com"
    captured = capsys.readouterr()
    assert "MX:" in captured.err
    assert "MX provider:" in captured.err


def test_detect_settings_mx_empty_no_provider(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Autoconfig miss, no MX records → None with a manual-config error."""
    provider = _detect_settings(
        email="user@example.com",
        autoconfig_lookup=_mock_autoconfig_none,
        mx_lookup=_mock_mx_empty,
        provider_from_mx=_mock_provider_from_mx,
    )
    assert provider is None
    captured = capsys.readouterr()
    assert "could not auto-detect settings for user@example.com" in captured.err
    assert "manually" in captured.err


def test_detect_settings_unknown_mx(capsys: pytest.CaptureFixture[str]) -> None:
    """Autoconfig miss, MX records for an unknown provider → None."""

    def _mx_lookup(email: str) -> list[str]:
        return ["mx.unknown.com"]

    def _provider_from_mx(mx_hosts: list[str]) -> MailProvider | None:
        return None  # unknown MX host

    provider = _detect_settings(
        email="user@example.com",
        autoconfig_lookup=_mock_autoconfig_none,
        mx_lookup=_mx_lookup,
        provider_from_mx=_provider_from_mx,
    )
    assert provider is None
    captured = capsys.readouterr()
    assert "MX: mx.unknown.com" in captured.err
    assert "could not auto-detect settings" in captured.err
