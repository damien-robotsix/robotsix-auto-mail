"""Manual host entry tests (_refine_manual)."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.cli.config import _refine_manual, _VerifyResult
from robotsix_auto_mail.config import MailConfig
from tests.cli.conftest import _refine_host_result, _refine_test_config


def test_refine_manual_updates_hosts(
    capsys: pytest.CaptureFixture[str], cfg: MailConfig
) -> None:
    """User provides new hosts → config updated."""
    result = _VerifyResult(imap_ok=False, smtp_ok=False, imap_error="refused")
    with mock.patch("builtins.input", side_effect=["new-imap.com", "new-smtp.com"]):
        outcome = _refine_manual(cfg, result)
    assert outcome.config is not None
    assert outcome.config.imap_host == "new-imap.com"
    assert outcome.config.smtp_host == "new-smtp.com"


def test_refine_manual_no_change(
    capsys: pytest.CaptureFixture[str], cfg: MailConfig
) -> None:
    """User presses Enter on both prompts → no config."""
    result = _VerifyResult(imap_ok=False, smtp_ok=False)
    with mock.patch("builtins.input", side_effect=["", ""]):
        outcome = _refine_manual(cfg, result)
    assert outcome.config is None


def test_refine_manual_eof(cfg: MailConfig) -> None:
    """EOFError during input → no config."""
    result = _VerifyResult(imap_ok=False, smtp_ok=False)
    with mock.patch("builtins.input", side_effect=EOFError):
        outcome = _refine_manual(cfg, result)
    assert outcome.config is None


def test_refine_manual_stops_when_prompt_returns_none() -> None:
    """_refine_manual signals stop when _prompt_hosts returns None."""
    from robotsix_auto_mail.cli import _refine_manual

    with mock.patch("robotsix_auto_mail.cli._prompt_hosts", return_value=None):
        outcome = _refine_manual(_refine_test_config(), _refine_host_result())

    assert outcome.config is None
