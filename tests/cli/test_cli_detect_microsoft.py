"""Tests for CLI detect subcommand Microsoft OAuth2 and app-password flows."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.cli import main
from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.config.detect import MailProvider
from tests.cli.conftest import _auth_fail_result, _ok_result


def test_detect_microsoft_runs_device_code_and_verifies(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """A Microsoft address runs device-code login and verifies over XOAUTH2,
    printing a JSON diagnostic report — never prompting for a password."""
    mock_provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch("getpass.getpass") as mock_getpass,
        mock.patch("robotsix_auto_mail.oauth2.device_code_login") as mock_login,
        mock.patch(
            "robotsix_auto_mail.cli._verify_config", return_value=_ok_result()
        ) as mock_verify,
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["detect", "user@contoso.com"])

    assert rc == 0
    mock_getpass.assert_not_called()
    mock_login.assert_called_once()
    mock_verify.assert_called_once()
    captured = capsys.readouterr()
    assert '"oauth2_provider": "microsoft"' in captured.out
    assert "Verification succeeded" in captured.err


def test_detect_microsoft_stdout_instructs_auth_login(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """For a Microsoft address the JSON report includes OAuth2 fields.
    When --no-verify is given the interactive device-code flow is skipped."""
    mock_provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch("getpass.getpass") as mock_getpass,
        mock.patch("robotsix_auto_mail.oauth2.device_code_login") as mock_login,
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["detect", "user@contoso.com", "--no-verify"])

    assert rc == 0
    mock_getpass.assert_not_called()
    mock_login.assert_not_called()
    captured = capsys.readouterr()
    assert '"oauth2_provider": "microsoft"' in captured.out


def test_detect_stdout_app_password_clears_oauth2_provider(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--app-password prints a config without oauth2_provider."""
    mock_provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            ["detect", "user@contoso.com", "--app-password", "--password", "", "--no-verify"]
        )

    assert rc == 0
    captured = capsys.readouterr()
    assert "Warning: --app-password" in captured.err
    # The printed report must NOT include oauth2_provider (cleared by --app-password).
    assert '"oauth2_provider"' not in captured.out
    # The non-Microsoft banner is used (microsoft was flipped to False)
    assert "No password provided" in captured.err


def test_detect_microsoft_auth_failure_points_at_auth_login(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """A Microsoft auth failure surfaces an actionable message and never
    re-prompts for a password."""
    mock_provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch("getpass.getpass") as mock_getpass,
        mock.patch("robotsix_auto_mail.oauth2.device_code_login"),
        mock.patch(
            "robotsix_auto_mail.cli._verify_config",
            return_value=_auth_fail_result(),
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["detect", "user@contoso.com"])

    assert rc == 1
    mock_getpass.assert_not_called()
    err = capsys.readouterr().err
    assert "auth login" in err


def test_detect_microsoft_custom_oauth2_client_id_and_tenant(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--oauth2-client-id and --oauth2-tenant are reflected in the JSON report
    and passed to device_code_login."""
    mock_provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch("getpass.getpass") as mock_getpass,
        mock.patch("robotsix_auto_mail.oauth2.device_code_login") as mock_login,
        mock.patch("robotsix_auto_mail.cli._verify_config", return_value=_ok_result()),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@tii.ae",
                "--oauth2-client-id",
                "12345678-1234-1234-1234-123456789abc",
                "--oauth2-tenant",
                "tii.ae",
            ]
        )

    assert rc == 0
    mock_getpass.assert_not_called()
    mock_login.assert_called_once()
    # Verify the MailConfig passed to device_code_login carries the custom
    # oauth2 settings (not just the YAML output).
    login_config = mock_login.call_args[0][0]
    assert isinstance(login_config, MailConfig)
    assert login_config.oauth2_client_id == "12345678-1234-1234-1234-123456789abc"
    assert login_config.oauth2_tenant == "tii.ae"
    captured = capsys.readouterr()
    assert '"oauth2_provider": "microsoft"' in captured.out
    assert '"oauth2_client_id": "12345678-1234-1234-1234-123456789abc"' in captured.out
    assert '"oauth2_tenant": "tii.ae"' in captured.out


def test_detect_microsoft_app_password_writes_password_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--app-password for a Microsoft host clears oauth2_provider, uses
    password-based auth (no device_code_login), and verifies."""
    mock_provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch("getpass.getpass", return_value="app-pw-123") as mock_getpass,
        mock.patch("robotsix_auto_mail.oauth2.device_code_login") as mock_login,
        mock.patch(
            "robotsix_auto_mail.cli._verify_config", return_value=_ok_result()
        ) as mock_verify,
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@tii.ae",
                "--app-password",
            ]
        )

    assert rc == 0
    mock_getpass.assert_called_once()
    mock_login.assert_not_called()
    mock_verify.assert_called_once()
    captured = capsys.readouterr()
    assert "Warning: --app-password" in captured.err
    assert "Verification succeeded" in captured.err
    # oauth2_provider is cleared in the JSON report (not present).
    assert '"oauth2_provider"' not in captured.out


def test_detect_app_password_mutually_exclusive_with_oauth2_flags(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--app-password + --oauth2-client-id is rejected."""
    mock_provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch("getpass.getpass"),
        mock.patch("robotsix_auto_mail.oauth2.device_code_login"),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@tii.ae",
                "--app-password",
                "--oauth2-client-id",
                "12345678-1234-1234-1234-123456789abc",
            ]
        )

    assert rc == 1
    err = capsys.readouterr().err
    assert "mutually exclusive" in err


def test_detect_app_password_noop_for_non_microsoft(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--app-password has no effect for non-Microsoft hosts (no warning,
    normal password flow)."""
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch("getpass.getpass", return_value="gm-pw") as mock_getpass,
        mock.patch(
            "robotsix_auto_mail.cli._verify_config", return_value=_ok_result()
        ) as mock_verify,
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--app-password",
            ]
        )

    assert rc == 0
    mock_getpass.assert_called_once()
    mock_verify.assert_called_once()
    err = capsys.readouterr().err
    assert "Warning: --app-password" not in err


def test_detect_app_password_noop_for_generic_imap_host(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--app-password has no effect for a generic non-Microsoft IMAP host
    (no warning, normal password flow)."""
    mock_provider = MailProvider(
        imap_host="imap.example.com", smtp_host="smtp.example.com"
    )

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch("getpass.getpass", return_value="example-pw") as mock_getpass,
        mock.patch(
            "robotsix_auto_mail.cli._verify_config", return_value=_ok_result()
        ) as mock_verify,
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@example.com",
                "--app-password",
            ]
        )

    assert rc == 0
    mock_getpass.assert_called_once()
    mock_verify.assert_called_once()
    err = capsys.readouterr().err
    assert "Warning: --app-password" not in err
