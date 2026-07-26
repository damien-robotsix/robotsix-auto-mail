"""Tests for CLI detect subcommand — interactive behaviour and error handling."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.cli import main
from robotsix_auto_mail.config.detect import MailProvider


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
        mock.patch(
            "robotsix_auto_mail.config.resolve_llm_api_key", return_value="sk-test"
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
    stdout = capsys.readouterr().out
    report = json.loads(stdout)
    assert report["login_ok"] is False
