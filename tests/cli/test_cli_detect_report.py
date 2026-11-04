"""Tests for CLI detect subcommand — report output."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.cli import main
from robotsix_auto_mail.config import MailAccountsConfig
from robotsix_auto_mail.detect import MailProvider
from tests.cli.conftest import _ok_result


def test_detect_prints_json_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect prints a JSON report to stdout with no secrets and writes no file."""
    output = tmp_path / "cfg.json"
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.detect.autoconfig_lookup",
            return_value=mock_provider,
        ),
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
    assert "imap.gmail.com" in content
    assert "user@gmail.com" in content


def test_detect_honours_id_in_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect --id does not affect the report (account id is informational)."""
    output = tmp_path / "cfg.json"
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.detect.autoconfig_lookup",
            return_value=mock_provider,
        ),
        mock.patch("robotsix_auto_mail.cli._verify_config", return_value=_ok_result()),
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
                "--output",
                str(output),
            ]
        )

    assert rc == 0
    content = output.read_text()
    # The detection key never lands in the written account.
    assert "sk-test" not in content

    accounts = MailAccountsConfig.model_validate(json.loads(output.read_text()))
    assert accounts.accounts[0].config.imap_host == "imap.gmail.com"
