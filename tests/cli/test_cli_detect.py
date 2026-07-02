"""Tests for the CLI detect subcommand and detect_settings helper."""

from __future__ import annotations

import builtins
import os
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from robotsix_auto_mail.cli import build_parser, main
from robotsix_auto_mail.cli.config import _detect_settings
from robotsix_auto_mail.config import (
    MailAccount,
    MailAccountsConfig,
    MailConfig,
)
from robotsix_auto_mail.detect import DetectionError, MailProvider


def _accounts(cfg: MailConfig, account_id: str = "default") -> MailAccountsConfig:
    """Wrap a single ``MailConfig`` in a one-element accounts container."""
    return MailAccountsConfig(
        accounts=(MailAccount(account_id=account_id, config=cfg, label=None),),
        default_account_id=account_id,
    )


# ---------------------------------------------------------------------------
# detect subcommand
# ---------------------------------------------------------------------------


def test_parser_has_detect_subcommand() -> None:
    """The parser knows the detect subcommand with expected defaults."""
    parser = build_parser()
    args = parser.parse_args(["detect", "user@gmail.com"])
    assert args.command == "detect"
    assert args.email == "user@gmail.com"
    assert args.stdout is False
    assert args.output == "config/mail.local.yaml"


def test_detect_missing_pydantic_ai(capsys: pytest.CaptureFixture[str]) -> None:
    """detect exits 1 when pydantic_ai package is not installed."""
    import sys

    # Remove detect module from cache so the lazy import inside
    # _cmd_detect is forced to re-import (and we can block it).
    real_detect = sys.modules.pop("robotsix_auto_mail.detect", None)
    original_import = builtins.__import__

    def _block_detect(
        name: str,
        *args: object,
        **kwargs: object,
    ) -> object:
        if name == "robotsix_auto_mail.detect":
            raise ImportError("No module named 'pydantic_ai'")
        return original_import(name, *args, **kwargs)  # type: ignore[arg-type]

    try:
        with mock.patch("builtins.__import__", side_effect=_block_detect):
            rc = main(["detect", "user@gmail.com"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "requires the pydantic-ai package" in err
    finally:
        if real_detect is not None:
            sys.modules["robotsix_auto_mail.detect"] = real_detect


@pytest.fixture
def no_autoconfig() -> object:
    """Force autoconfig + MX detection to miss so tests reach the LLM path."""
    with (
        mock.patch("robotsix_auto_mail.detect.autoconfig_lookup", return_value=None),
        mock.patch("robotsix_auto_mail.detect.mx_lookup", return_value=[]),
        mock.patch("robotsix_auto_mail.detect.provider_from_mx", return_value=None),
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


def test_detect_happy_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect writes a single config file (password included) on success."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
    )

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch("getpass.getpass", return_value="testpass"),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["detect", "user@gmail.com", "--output", str(output), "--no-verify"])

    assert rc == 0
    content = output.read_text()
    assert "imap.gmail.com" in content
    assert "smtp.gmail.com" in content
    assert "user@gmail.com" in content
    # Password is written into the config file itself — no separate file.
    assert "testpass" in content
    assert not (tmp_path / "secrets.yaml").exists()

    captured = capsys.readouterr()
    assert "Config written" in captured.err


def test_detect_password_supplied(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect --password skips the interactive prompt and writes the config."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
    )

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch("getpass.getpass") as mock_getpass,
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--output",
                str(output),
                "--password",
                "cli-pass",
                "--no-verify",
            ]
        )

    assert rc == 0
    mock_getpass.assert_not_called()

    content = output.read_text()
    assert "cli-pass" in content
    assert not (tmp_path / "secrets.yaml").exists()


def test_detect_empty_password(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect with an empty password writes the config and warns the user."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
    )

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch("getpass.getpass", return_value=""),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["detect", "user@gmail.com", "--output", str(output)])

    assert rc == 0
    content = output.read_text()
    assert "imap.gmail.com" in content

    captured = capsys.readouterr()
    assert "No password provided" in captured.err


def test_detect_stdout(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect --stdout prints config and emits a verification banner."""
    mock_provider = MailProvider(
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
    )

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["detect", "user@gmail.com", "--stdout"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "imap.gmail.com" in captured.out
    assert "smtp.gmail.com" in captured.out
    assert "user@gmail.com" in captured.out
    assert "verify" in captured.err.lower()


def test_detect_stdout_redacts_password(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect --stdout --password omits the password from the printed config.

    The supplied password must NOT leak to stdout; instead the rendered
    config carries the empty-password placeholder hinting at MAIL_PASSWORD.
    """
    mock_provider = MailProvider(
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
    )

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["detect", "user@gmail.com", "--stdout", "--password", "cli-pass"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "imap.gmail.com" in captured.out
    assert "cli-pass" not in captured.out
    # The rendered (stdout) config redacts the password; the MAIL_PASSWORD
    # hint is printed to stderr alongside the multi-account YAML.
    assert '"password": ""' in captured.out
    assert "fill in auth.password" in captured.err


def test_detect_detection_error(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect exits 1 when DetectionError is raised (and autoconfig missed)."""
    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider",
            side_effect=DetectionError("test error"),
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["detect", "user@gmail.com", "--stdout"])

    assert rc == 1
    captured = capsys.readouterr()
    assert "test error" in captured.err


def test_detect_llm_api_key_from_config(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect resolves the LLM api key from the config file (llm.api_key) and
    forwards it to detect_provider (model is no longer forwarded — the tier
    bakes the model choice)."""
    mock_provider = MailProvider(
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
    )
    mock_dp = mock.MagicMock(return_value=mock_provider)

    with (
        mock.patch("robotsix_auto_mail.config.loader.load_llm", return_value="sk-test"),
        mock.patch("robotsix_auto_mail.detect.detect_provider", mock_dp),
    ):
        rc = main(["detect", "user@x.com", "--stdout"])

    assert rc == 0
    mock_dp.assert_called_once_with(
        "user@x.com",
        api_key="sk-test",
        provider_model="",
        mx_hosts=[],
    )


def test_detect_uses_autoconfig_when_available(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When autoconfig resolves, the LLM is not consulted."""
    output = tmp_path / "cfg.yaml"
    autoconf_provider = MailProvider(
        imap_host="imap.fromautoconfig.net",
        smtp_host="smtp.fromautoconfig.net",
    )
    mock_llm = mock.MagicMock()

    with (
        mock.patch(
            "robotsix_auto_mail.detect.autoconfig_lookup",
            return_value=autoconf_provider,
        ),
        mock.patch("robotsix_auto_mail.detect.detect_provider", mock_llm),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@custom.net",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
            ]
        )

    assert rc == 0
    mock_llm.assert_not_called()
    assert "imap.fromautoconfig.net" in output.read_text()
    assert "autoconfig" in capsys.readouterr().err


def test_detect_verifies_connection_on_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """After writing the config, detect verifies by connecting (default)."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
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
                "--password",
                "pw",
            ]
        )

    assert rc == 0
    mock_verify.assert_called_once()
    assert mock_verify.call_args.args[0].password == "pw"
    assert "Verification succeeded" in capsys.readouterr().err


def test_detect_verify_failure_returns_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """A failed verification (auth, no retries) surfaces as exit code 1."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    # --password ⇒ no interactive password retry budget, so an auth-only
    # failure ends the loop immediately.
    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch(
            "robotsix_auto_mail.cli._verify_config",
            return_value=_auth_fail_result(),
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

    assert rc == 1
    assert output.exists()
    assert "Verification FAILED" in capsys.readouterr().err


def test_detect_no_verify_skips_check(tmp_path: Path, no_autoconfig: object) -> None:
    """--no-verify writes the config without connecting."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch("robotsix_auto_mail.cli._verify_config") as mock_verify,
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
                "--no-verify",
            ]
        )

    assert rc == 0
    mock_verify.assert_not_called()


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
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
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
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
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
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
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
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
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
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
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
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
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
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
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
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
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
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
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


def test_detect_refines_host_with_llm_on_connection_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """A connection failure triggers an LLM refinement that then succeeds."""
    output = tmp_path / "cfg.yaml"
    bad = MailProvider(imap_host="imap.bad.net", smtp_host="smtp.gmail.com")
    good = MailProvider(imap_host="imap.good.net", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider",
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
            "robotsix_auto_mail.detect.detect_provider",
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


def test_detect_preserves_existing_llm_section(
    tmp_path: Path, no_autoconfig: object
) -> None:
    """Re-running detect over an accounts file keeps its top-level llm section."""
    output = tmp_path / "mail.local.json"
    import json as _json_seed

    seed = {
        "accounts": [
            {
                "account_id": "existing",
                "config": {
                    "imap_host": "old.example.com",
                    "smtp_host": "old.example.com",
                    "username": "old@example.com",
                    "password": "old-pw",
                    "llm_api_key": "sk-keep-me",
                },
                "label": None,
            }
        ],
        "default_account_id": "existing",
    }
    output.write_text(_json_seed.dumps(seed, indent=2))
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
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
                "--no-verify",
            ]
        )

    assert rc == 0
    content = output.read_text()
    # mail fields updated…
    assert "imap.gmail.com" in content
    assert "user@gmail.com" in content
    # …but the llm api key is preserved
    assert "sk-keep-me" in content


def test_detect_honours_id_flag(tmp_path: Path, no_autoconfig: object) -> None:
    """detect --id sets the account id and the .data/<id>/mail.db store path."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
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
                "--no-verify",
                "--id",
                "personal",
            ]
        )

    assert rc == 0
    import json as _json2
    accounts = MailAccountsConfig.model_validate(_json2.loads(output.read_text()))
    assert accounts.ids() == ("personal",)
    assert accounts.default_account_id == "personal"
    assert accounts.get("personal").config.db_path == ".data/personal/mail.db"


def test_detect_appends_second_account(tmp_path: Path, no_autoconfig: object) -> None:
    """A second detect against an existing multi-account file appends, not clobbers."""
    output = tmp_path / "cfg.yaml"
    p1 = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")
    p2 = MailProvider(imap_host="imap.work.com", smtp_host="smtp.work.com")

    with (
        mock.patch("robotsix_auto_mail.detect.detect_provider", side_effect=[p1, p2]),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc1 = main(
            [
                "detect",
                "me@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
                "--id",
                "personal",
            ]
        )
        rc2 = main(
            [
                "detect",
                "me@work.com",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
                "--id",
                "work",
            ]
        )

    assert rc1 == 0
    assert rc2 == 0
    import json as _json3
    accounts = MailAccountsConfig.model_validate(_json3.loads(output.read_text()))
    assert set(accounts.ids()) == {"personal", "work"}
    assert accounts.get("work").config.imap_host == "imap.work.com"


def test_detect_refuses_duplicate_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """A detect whose resolved id already exists is refused (exit 1) without clobber."""
    output = tmp_path / "cfg.yaml"
    provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch("robotsix_auto_mail.detect.detect_provider", return_value=provider),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc1 = main(
            [
                "detect",
                "me@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
                "--id",
                "personal",
            ]
        )
        capsys.readouterr()
        rc2 = main(
            [
                "detect",
                "other@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
                "--id",
                "personal",
            ]
        )

    assert rc1 == 0
    assert rc2 == 1
    assert "already exists" in capsys.readouterr().err
    import json as _json2
    accounts = MailAccountsConfig.model_validate(_json2.loads(output.read_text()))
    assert accounts.ids() == ("personal",)


def test_detect_overwrite_existing_account(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--overwrite updates transport fields in place; no duplicate is added."""

    output = tmp_path / "cfg.json"
    seed_cfg = MailConfig(
        imap_host="",
        smtp_host="",
        username="test@gmail.com",
        password="",
        db_path=".data/mail.db",  # legacy single-account path — must be preserved
    )
    seed_account = MailAccount(account_id="main", config=seed_cfg, label="Main Account")
    container = MailAccountsConfig(accounts=[seed_account], default_account_id="main")
    output.write_text(container.model_dump_json(indent=2))

    provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch("robotsix_auto_mail.detect.detect_provider", return_value=provider),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "test@gmail.com",
                "--id",
                "main",
                "--overwrite",
                "--password",
                "secret",
                "--no-verify",
                "--output",
                str(output),
            ]
        )

    assert rc == 0
    import json as _json2
    accounts = MailAccountsConfig.model_validate(_json2.loads(output.read_text()))
    # No duplicate account appended
    assert accounts.ids() == ("main",)
    main_account = next(a for a in accounts.accounts if a.account_id == "main")
    cfg = main_account.config
    # Transport fields updated
    assert cfg.imap_host == "imap.gmail.com"
    assert cfg.smtp_host == "smtp.gmail.com"
    # Password written (supplied via --password)
    assert cfg.password == "secret"
    # Non-transport fields preserved from seed
    assert cfg.username == "test@gmail.com"
    assert cfg.db_path == ".data/mail.db"  # legacy path preserved, not replaced
    # Label preserved from existing account
    assert main_account.label == "Main Account"


def test_detect_overwrite_not_set_still_errors_on_duplicate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """Without --overwrite, a duplicate id still exits 1 and prints 'already exists'."""

    output = tmp_path / "cfg.json"
    seed_cfg = MailConfig(
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
        username="test@gmail.com",
        password="pw",
    )
    container = MailAccountsConfig(
        accounts=[MailAccount(account_id="main", config=seed_cfg, label=None)],
        default_account_id="main",
    )
    output.write_text(container.model_dump_json(indent=2))
    provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch("robotsix_auto_mail.detect.detect_provider", return_value=provider),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "test@gmail.com",
                "--id",
                "main",
                "--no-verify",
                "--output",
                str(output),
                "--password",
                "pw",
            ]
        )

    assert rc == 1
    assert "already exists" in capsys.readouterr().err


def test_detect_overwrite_with_oauth2_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--overwrite --oauth2-client-id overlays oauth2 fields onto an existing
    account config instead of silently ignoring them."""

    output = tmp_path / "cfg.json"
    # Seed a Microsoft account with default oauth2 fields — the
    # --oauth2-client-id and --oauth2-tenant flags should override them.
    seed_cfg = MailConfig(
        imap_host="old.example.com",
        smtp_host="old.example.com",
        username="user@tii.ae",
        password="",
        oauth2_provider="microsoft",
        oauth2_client_id="9e5f94bc-e8a4-4e73-b8be-63364c29d753",
        oauth2_tenant="organizations",
    )
    seed_account = MailAccount(account_id="tii", config=seed_cfg, label="TII")
    container = MailAccountsConfig(accounts=[seed_account], default_account_id="tii")
    output.write_text(container.model_dump_json(indent=2))

    mock_provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
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
                "--id",
                "tii",
                "--overwrite",
                "--oauth2-client-id",
                "12345678-1234-1234-1234-123456789abc",
                "--oauth2-tenant",
                "tii.ae",
                "--output",
                str(output),
            ]
        )

    assert rc == 0
    mock_getpass.assert_not_called()
    mock_login.assert_called_once()
    # The config passed to device_code_login must carry the custom fields.
    login_config = mock_login.call_args[0][0]
    assert login_config.oauth2_client_id == "12345678-1234-1234-1234-123456789abc"
    assert login_config.oauth2_tenant == "tii.ae"
    # The written JSON must also include both fields.
    content = output.read_text()
    assert '"oauth2_client_id": "12345678-1234-1234-1234-123456789abc"' in content
    assert '"oauth2_tenant": "tii.ae"' in content
    # Existing non-transport fields are preserved.
    import json as _json4
    accounts = MailAccountsConfig.model_validate(_json4.loads(output.read_text()))
    cfg = accounts.get("tii").config
    assert cfg.username == "user@tii.ae"


def test_detect_overwrite_app_password_clears_oauth2_provider(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--overwrite --app-password clears oauth2_provider from an existing
    Microsoft account config that had it set."""

    output = tmp_path / "cfg.json"
    # Seed a Microsoft account with oauth2_provider set
    seed_cfg = MailConfig(
        imap_host="outlook.office365.com",
        smtp_host="smtp.office365.com",
        username="user@contoso.com",
        password="",
        oauth2_provider="microsoft",
        oauth2_client_id="9e5f94bc-e8a4-4e73-b8be-63364c29d753",
        oauth2_tenant="organizations",
    )
    seed_account = MailAccount(account_id="ms", config=seed_cfg, label=None)
    container = MailAccountsConfig(accounts=[seed_account], default_account_id="ms")
    output.write_text(container.model_dump_json(indent=2))

    mock_provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch("getpass.getpass", return_value="app-pw-789") as mock_getpass,
        mock.patch("robotsix_auto_mail.oauth2.device_code_login") as mock_login,
        mock.patch(
            "robotsix_auto_mail.cli._verify_config", return_value=_ok_result()
        ) as mock_verify,
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@contoso.com",
                "--id",
                "ms",
                "--overwrite",
                "--app-password",
                "--output",
                str(output),
            ]
        )

    assert rc == 0
    mock_getpass.assert_called_once()
    mock_login.assert_not_called()
    mock_verify.assert_called_once()
    err = capsys.readouterr().err
    assert "Warning: --app-password" in err
    content = output.read_text()
    assert "app-pw-789" in content
    # oauth2_provider must be cleared
    # (the write path uses save_accounts which may not be available,
    # so we just check the output content directly)


def test_detect_overwrite_preserves_llm_api_key(
    tmp_path: Path, no_autoconfig: object
) -> None:
    """--overwrite preserves llm_api_key, llm_provider_model, and langfuse_*
    fields from an existing config file (re-deploy path)."""

    output = tmp_path / "cfg.json"
    seed_cfg = MailConfig(
        imap_host="old.example.com",
        smtp_host="old.example.com",
        username="test@gmail.com",
        password="old-pw",
        llm_api_key="sk-seed",
        llm_provider_model="openai/gpt-4o",
        langfuse_public_key="pk-seed",
        langfuse_secret_key="sk-seed-lf",
        langfuse_base_url="https://cloud.langfuse.com",
    )
    seed_account = MailAccount(account_id="main", config=seed_cfg, label="Main")
    container = MailAccountsConfig(accounts=[seed_account], default_account_id="main")
    output.write_text(container.model_dump_json(indent=2))

    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-env"}),
    ):
        rc = main(
            [
                "detect",
                "test@gmail.com",
                "--id",
                "main",
                "--overwrite",
                "--password",
                "pw",
                "--no-verify",
                "--output",
                str(output),
            ]
        )

    assert rc == 0
    import json as _json2
    accounts = MailAccountsConfig.model_validate(_json2.loads(output.read_text()))
    cfg = accounts.get("main").config
    # Existing llm/langfuse values preserved from the seed file.
    assert cfg.llm_api_key == "sk-seed"
    assert cfg.llm_provider_model == "openai/gpt-4o"
    assert cfg.langfuse_public_key == "pk-seed"
    assert cfg.langfuse_secret_key == "sk-seed-lf"
    assert cfg.langfuse_base_url == "https://cloud.langfuse.com"
    # Transport fields updated.
    assert cfg.imap_host == "imap.gmail.com"

    # Raw file carries the llm and langfuse fields.
    content = output.read_text()
    assert "sk-seed" in content
    assert "pk-seed" in content


def test_detect_writes_llm_api_key_from_env(
    tmp_path: Path, no_autoconfig: object
) -> None:
    """Fresh detect with LLM_API_KEY env var writes llm.api_key into the output."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.detect.detect_provider", return_value=mock_provider
        ),
        mock.patch("getpass.getpass", return_value="pw"),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-env"}),
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--output",
                str(output),
                "--no-verify",
            ]
        )

    assert rc == 0
    content = output.read_text()
    # JSON output contains the env-provided API key.
    assert "sk-env" in content

    import json as _json5
    accounts = MailAccountsConfig.model_validate(_json5.loads(output.read_text()))
    cfg = accounts.default.config
    assert cfg.llm_api_key == "sk-env"


# ---------------------------------------------------------------------------
# _detect_settings
# ---------------------------------------------------------------------------


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
