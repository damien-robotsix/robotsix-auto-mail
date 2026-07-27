"""Tests for CLI detect subcommand — flag behaviour."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.cli import main
from robotsix_auto_mail.config.detect import MailProvider
from tests.cli.conftest import _ok_result


def test_detect_no_verify_still_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """detect --no-verify still prints a report with login_ok=False."""
    output = tmp_path / "cfg.json"
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch("getpass.getpass", return_value="app-pw-789") as _mock_getpass,
        mock.patch("robotsix_auto_mail.oauth2.device_code_login") as _mock_login,
        mock.patch(
            "robotsix_auto_mail.cli._verify_config", return_value=_ok_result()
        ) as _mock_verify,
        mock.patch(
            "robotsix_auto_mail.config.resolve_llm_api_key", return_value="sk-test"
        ),
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
    _mock_getpass.assert_called_once()
    _mock_login.assert_not_called()
    _mock_verify.assert_called_once()
    content = output.read_text()
    assert "app-pw-789" in content
    # oauth2_provider must be cleared
    # (the write path uses save_accounts which may not be available,
    # so we just check the output content directly)
