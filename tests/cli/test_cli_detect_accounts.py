"""Tests for CLI detect subcommand — report-only output."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.cli import main
from robotsix_auto_mail.config.detect import MailProvider
from tests.cli.conftest import _ok_result


def test_detect_prints_json_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect prints a JSON report to stdout with no secrets and writes no file."""
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch(
            "robotsix_auto_mail.cli._verify_config", return_value=_ok_result()
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

    assert rc == 0
    stdout = capsys.readouterr().out
    report = json.loads(stdout)
    assert report["imap_host"] == "imap.gmail.com"
    assert report["smtp_host"] == "smtp.gmail.com"
    assert report["username"] == "user@gmail.com"
    # Password must never appear in the report.
    assert "pw" not in json.dumps(report)
    assert "password" not in report


def test_detect_no_verify_still_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect --no-verify still prints a report with login_ok=False."""
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
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
    stdout = capsys.readouterr().out
    report = json.loads(stdout)
    assert report["imap_host"] == "imap.gmail.com"
    assert report["login_ok"] is False


def test_detect_verification_failure_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect with failed verification still prints a report (login_ok=False)."""
    mock_provider = MailProvider(imap_host="imap.bad.com", smtp_host="smtp.bad.com")

    from robotsix_auto_mail.cli import _VerifyResult

    fail = _VerifyResult(
        imap_ok=False,
        smtp_ok=False,
        imap_error="refused",
        smtp_error="timeout",
    )

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch(
            "robotsix_auto_mail.cli._verify_config",
            side_effect=[fail, fail, fail, fail, fail],
        ),
        mock.patch("builtins.input", side_effect=["", ""]),
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
    stdout = capsys.readouterr().out
    report = json.loads(stdout)
    assert report["login_ok"] is False


def test_detect_honours_id_in_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect --id does not affect the report (account id is informational)."""
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch(
            "robotsix_auto_mail.cli._verify_config", return_value=_ok_result()
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--password",
                "pw",
                "--id",
                "personal",
                "--no-verify",
            ]
        )

    assert rc == 0
