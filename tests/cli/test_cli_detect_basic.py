"""Tests for basic CLI detect subcommand functionality and connection verification."""

from __future__ import annotations

import builtins
import os
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.cli import build_parser, main
from robotsix_auto_mail.config.detect import DetectionError, MailProvider
from tests.cli.conftest import _ok_result, _auth_fail_result


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
    real_detect = sys.modules.pop("robotsix_auto_mail.config.detect", None)
    original_import = builtins.__import__

    def _block_detect(
        name: str,
        *args: object,
        **kwargs: object,
    ) -> object:
        if name == "robotsix_auto_mail.config.detect":
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
            sys.modules["robotsix_auto_mail.config.detect"] = real_detect


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
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
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
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
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
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
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
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
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
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
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
            "robotsix_auto_mail.config.detect.detect_provider",
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
        mock.patch("robotsix_auto_mail.config.detect.detect_provider", mock_dp),
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
            "robotsix_auto_mail.config.detect.autoconfig_lookup",
            return_value=autoconf_provider,
        ),
        mock.patch("robotsix_auto_mail.config.detect.detect_provider", mock_llm),
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
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
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
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
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
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
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
