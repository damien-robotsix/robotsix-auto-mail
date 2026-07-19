"""Tests for basic CLI detect subcommand functionality and connection verification.

Updated for report-only detect: no --output/--stdout flags; JSON diagnostic
report is always printed to stdout.
"""

from __future__ import annotations

import builtins
import json
import os
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.cli import build_parser, main
from robotsix_auto_mail.config.detect import DetectionError, MailProvider
from tests.cli.conftest import _auth_fail_result, _ok_result


def test_parser_has_detect_subcommand() -> None:
    """The parser knows the detect subcommand with expected arguments.

    --output and --stdout have been removed; detect always prints a JSON
    diagnostic report to stdout.
    """
    parser = build_parser()
    args = parser.parse_args(["detect", "user@gmail.com"])
    assert args.command == "detect"
    assert args.email == "user@gmail.com"
    assert args.stdout is False
    assert args.output == ""


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
    """detect prints a JSON diagnostic report to stdout on success."""
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
        rc = main(["detect", "user@gmail.com", "--no-verify"])

    assert rc == 0
    stdout = capsys.readouterr().out
    report = json.loads(stdout)
    assert report["imap_host"] == "imap.gmail.com"
    assert report["smtp_host"] == "smtp.gmail.com"
    assert report["username"] == "user@gmail.com"
    # Password must never appear in the report.
    assert "testpass" not in json.dumps(report)
    assert "password" not in report


def test_detect_password_supplied(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect --password skips the interactive prompt and prints a report."""
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
                "--password",
                "cli-pass",
                "--no-verify",
            ]
        )

    assert rc == 0
    mock_getpass.assert_not_called()

    stdout = capsys.readouterr().out
    report = json.loads(stdout)
    assert report["imap_host"] == "imap.gmail.com"
    assert "cli-pass" not in json.dumps(report)


def test_detect_empty_password(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect with an empty password prints a report and warns the user."""
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
        rc = main(["detect", "user@gmail.com"])

    assert rc == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["imap_host"] == "imap.gmail.com"
    assert "No password provided" in captured.err


def test_detect_stdout(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect prints JSON report to stdout by default (--stdout removed)."""
    mock_provider = MailProvider(
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
    )

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch("getpass.getpass", return_value="pw"),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(["detect", "user@gmail.com", "--no-verify"])

    assert rc == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["imap_host"] == "imap.gmail.com"
    assert report["smtp_host"] == "smtp.gmail.com"
    assert report["username"] == "user@gmail.com"
    # The report always carries instructions on stderr.
    assert "Copy the settings above" in captured.err


def test_detect_stdout_redacts_password(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect --password omits the password from the printed JSON report."""
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
        rc = main(["detect", "user@gmail.com", "--password", "cli-pass", "--no-verify"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "imap.gmail.com" in captured.out
    assert "cli-pass" not in captured.out
    # The report never includes a "password" key.
    report = json.loads(captured.out)
    assert "password" not in report


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
        rc = main(["detect", "user@gmail.com"])

    assert rc == 1
    captured = capsys.readouterr()
    assert "test error" in captured.err


def test_detect_llm_api_key_from_config(
    capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect resolves the LLM api key from the config file and forwards it
    to detect_provider."""
    mock_provider = MailProvider(
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
    )
    mock_dp = mock.MagicMock(return_value=mock_provider)

    with (
        mock.patch("robotsix_auto_mail.config.loader.load_llm", return_value="sk-test"),
        mock.patch("robotsix_auto_mail.config.detect.detect_provider", mock_dp),
    ):
        rc = main(["detect", "user@x.com", "--password", "pw", "--no-verify"])

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
                "--password",
                "pw",
                "--no-verify",
            ]
        )

    assert rc == 0
    mock_llm.assert_not_called()
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["imap_host"] == "imap.fromautoconfig.net"
    assert "autoconfig" in captured.err


def test_detect_verifies_connection_on_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """After detection, detect verifies by connecting (default)."""
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
                "--password",
                "pw",
            ]
        )

    assert rc == 0
    mock_verify.assert_called_once()
    assert mock_verify.call_args.args[0].password.get_secret_value() == "pw"
    assert "Verification succeeded" in capsys.readouterr().err


def test_detect_verify_failure_returns_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """A failed verification (auth, no retries) surfaces as exit code 1."""
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
                "--password",
                "pw",
            ]
        )

    assert rc == 1
    captured = capsys.readouterr()
    assert "Verification FAILED" in captured.err
    # Report is still printed with login_ok=False.
    report = json.loads(captured.out)
    assert report["login_ok"] is False


def test_detect_no_verify_skips_check(tmp_path: Path, no_autoconfig: object) -> None:
    """--no-verify prints the report without connecting."""
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
                "--password",
                "pw",
                "--no-verify",
            ]
        )

    assert rc == 0
    mock_verify.assert_not_called()
