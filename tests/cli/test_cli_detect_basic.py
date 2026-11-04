"""Tests for basic CLI detect subcommand functionality and connection verification.

Updated for report-only detect: no --output/--stdout flags; JSON diagnostic
report is always printed to stdout.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.cli import build_parser, main
from robotsix_auto_mail.detect import MailProvider
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
            "robotsix_auto_mail.detect.autoconfig_lookup",
            return_value=mock_provider,
        ),
        mock.patch("getpass.getpass", return_value="testpass"),
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
            "robotsix_auto_mail.detect.autoconfig_lookup",
            return_value=mock_provider,
        ),
        mock.patch("getpass.getpass") as mock_getpass,
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
            "robotsix_auto_mail.detect.autoconfig_lookup",
            return_value=mock_provider,
        ),
        mock.patch("getpass.getpass", return_value=""),
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
            "robotsix_auto_mail.detect.autoconfig_lookup",
            return_value=mock_provider,
        ),
        mock.patch("getpass.getpass", return_value="pw"),
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
            "robotsix_auto_mail.detect.autoconfig_lookup",
            return_value=mock_provider,
        ),
    ):
        rc = main(["detect", "user@gmail.com", "--password", "cli-pass", "--no-verify"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "imap.gmail.com" in captured.out
    assert "cli-pass" not in captured.out
    # The report never includes a "password" key.
    report = json.loads(captured.out)
    assert "password" not in report


def test_detect_uses_autoconfig_when_available(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When autoconfig resolves, the MX lookup is not consulted."""
    autoconf_provider = MailProvider(
        imap_host="imap.fromautoconfig.net",
        smtp_host="smtp.fromautoconfig.net",
    )
    mock_mx = mock.MagicMock(return_value=[])

    with (
        mock.patch(
            "robotsix_auto_mail.detect.autoconfig_lookup",
            return_value=autoconf_provider,
        ),
        mock.patch("robotsix_auto_mail.detect.mx_lookup", mock_mx),
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
    mock_mx.assert_not_called()
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
            "robotsix_auto_mail.detect.autoconfig_lookup",
            return_value=mock_provider,
        ),
        mock.patch(
            "robotsix_auto_mail.cli._verify_config", return_value=_ok_result()
        ) as mock_verify,
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
            "robotsix_auto_mail.detect.autoconfig_lookup",
            return_value=mock_provider,
        ),
        mock.patch(
            "robotsix_auto_mail.cli._verify_config",
            return_value=_auth_fail_result(),
        ),
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
            "robotsix_auto_mail.detect.autoconfig_lookup",
            return_value=mock_provider,
        ),
        mock.patch("robotsix_auto_mail.cli._verify_config") as mock_verify,
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
