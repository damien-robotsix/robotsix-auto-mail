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
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """A Microsoft address writes an OAuth2 block, runs device-code login, and
    verifies over XOAUTH2 — never prompting for or writing a password."""
    output = tmp_path / "cfg.yaml"
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
        rc = main(["detect", "user@contoso.com", "--output", str(output)])

    assert rc == 0
    mock_getpass.assert_not_called()
    mock_login.assert_called_once()
    mock_verify.assert_called_once()
    content = output.read_text()
    assert '"oauth2_provider": "microsoft"' in content
    assert '"password": ""' in content
    assert "Verification succeeded" in capsys.readouterr().err


def test_detect_microsoft_stdout_instructs_auth_login(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--stdout for a Microsoft address emits the OAuth2 YAML and tells the
    user to run `auth login`, without any interactive flow."""
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
        rc = main(["detect", "user@contoso.com", "--stdout"])

    assert rc == 0
    mock_getpass.assert_not_called()
    mock_login.assert_not_called()
    captured = capsys.readouterr()
    assert '"oauth2_provider": "microsoft"' in captured.out
    assert '"password": ""' in captured.out
    assert "auth login" in captured.err


def test_detect_stdout_app_password_clears_oauth2_provider(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--stdout --app-password prints a config without oauth2_provider."""
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
        rc = main(["detect", "user@contoso.com", "--stdout", "--app-password"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "Warning: --app-password" in captured.err
    # The printed config must clear oauth2_provider (empty string).
    assert '"oauth2_provider": ""' in captured.out
    # The non-Microsoft banner is used (microsoft was flipped to False)
    assert "fill in auth.password" in captured.err


def test_detect_microsoft_auth_failure_points_at_auth_login(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """A Microsoft auth failure surfaces an actionable message and never
    re-prompts for a password."""
    output = tmp_path / "cfg.yaml"
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
        rc = main(["detect", "user@contoso.com", "--output", str(output)])

    assert rc == 1
    mock_getpass.assert_not_called()
    err = capsys.readouterr().err
    assert "auth login" in err


def test_detect_microsoft_custom_oauth2_client_id_and_tenant(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--oauth2-client-id and --oauth2-tenant are written to config and
    passed to device_code_login."""
    output = tmp_path / "cfg.yaml"
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
                "--output",
                str(output),
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
    content = output.read_text()
    assert '"oauth2_provider": "microsoft"' in content
    assert '"oauth2_client_id": "12345678-1234-1234-1234-123456789abc"' in content
    assert '"oauth2_tenant": "tii.ae"' in content


def test_detect_microsoft_app_password_writes_password_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--app-password for a Microsoft host clears oauth2_provider, writes
    a password, and uses password-based auth (no device_code_login)."""
    output = tmp_path / "cfg.yaml"
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
                "--output",
                str(output),
                "--app-password",
            ]
        )

    assert rc == 0
    mock_getpass.assert_called_once()
    mock_login.assert_not_called()
    mock_verify.assert_called_once()
    err = capsys.readouterr().err
    assert "Warning: --app-password" in err
    assert "Verification succeeded" in err
    content = output.read_text()
    assert "app-pw-123" in content
    assert "oauth2_provider:" not in content


def test_detect_app_password_mutually_exclusive_with_oauth2_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--app-password + --oauth2-client-id is rejected."""
    output = tmp_path / "cfg.yaml"
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
                "--output",
                str(output),
                "--app-password",
                "--oauth2-client-id",
                "12345678-1234-1234-1234-123456789abc",
            ]
        )

    assert rc == 1
    err = capsys.readouterr().err
    assert "mutually exclusive" in err


def test_detect_app_password_noop_for_non_microsoft(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--app-password has no effect for non-Microsoft hosts (no warning,
    normal password flow)."""
    output = tmp_path / "cfg.yaml"
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
                "--output",
                str(output),
                "--app-password",
            ]
        )

    assert rc == 0
    mock_getpass.assert_called_once()
    mock_verify.assert_called_once()
    err = capsys.readouterr().err
    assert "Warning: --app-password" not in err
    content = output.read_text()
    assert "gm-pw" in content
    assert "oauth2_provider:" not in content


def test_detect_app_password_noop_for_generic_imap_host(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--app-password has no effect for a generic non-Microsoft IMAP host
    (no warning, normal password flow)."""
    output = tmp_path / "cfg.yaml"
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
                "--output",
                str(output),
                "--app-password",
            ]
        )

    assert rc == 0
    mock_getpass.assert_called_once()
    mock_verify.assert_called_once()
    err = capsys.readouterr().err
    assert "Warning: --app-password" not in err
    content = output.read_text()
    assert "example-pw" in content
    assert "oauth2_provider:" not in content
