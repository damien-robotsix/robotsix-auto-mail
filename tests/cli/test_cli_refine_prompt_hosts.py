"""Unit tests for _prompt_hosts."""

from __future__ import annotations

from unittest import mock

from robotsix_auto_mail.cli.config import _prompt_hosts, _VerifyResult
from robotsix_auto_mail.config import MailConfig


def test_prompt_hosts_both_failing(cfg: MailConfig) -> None:
    """Both IMAP and SMTP host problems prompt for each host."""
    result = _VerifyResult(
        imap_ok=False, smtp_ok=False, imap_error="refused", smtp_error="timeout"
    )
    with mock.patch("builtins.input", side_effect=["new-imap.com", "new-smtp.com"]):
        updated = _prompt_hosts(cfg, result)
    assert updated is not None
    assert updated.imap_host == "new-imap.com"
    assert updated.smtp_host == "new-smtp.com"


def test_prompt_hosts_imap_only(cfg: MailConfig) -> None:
    """Only IMAP has a host problem → only IMAP is prompted."""
    result = _VerifyResult(imap_ok=False, smtp_ok=True, imap_error="refused")
    with mock.patch("builtins.input", side_effect=["fixed-imap.com"]):
        updated = _prompt_hosts(cfg, result)
    assert updated is not None
    assert updated.imap_host == "fixed-imap.com"
    assert updated.smtp_host == cfg.smtp_host


def test_prompt_hosts_smtp_only(cfg: MailConfig) -> None:
    """Only SMTP has a host problem → only SMTP is prompted."""
    result = _VerifyResult(imap_ok=True, smtp_ok=False, smtp_error="timeout")
    with mock.patch("builtins.input", side_effect=["fixed-smtp.com"]):
        updated = _prompt_hosts(cfg, result)
    assert updated is not None
    assert updated.smtp_host == "fixed-smtp.com"
    assert updated.imap_host == cfg.imap_host


def test_prompt_hosts_auth_not_prompted(cfg: MailConfig) -> None:
    """Auth failures (not host problems) are not prompted."""
    result = _VerifyResult(
        imap_ok=False,
        smtp_ok=False,
        imap_auth=True,
        smtp_auth=True,
        imap_error="auth",
        smtp_error="auth",
    )
    updated = _prompt_hosts(cfg, result)
    assert updated is None  # no host problems → no prompts → no change


def test_prompt_hosts_no_change(cfg: MailConfig) -> None:
    """User presses Enter without typing → no config returned."""
    result = _VerifyResult(imap_ok=False, smtp_ok=False)
    with mock.patch("builtins.input", side_effect=["", ""]):
        updated = _prompt_hosts(cfg, result)
    assert updated is None


def test_prompt_hosts_eof(cfg: MailConfig) -> None:
    """EOFError during prompt → None returned."""
    result = _VerifyResult(imap_ok=False, smtp_ok=False)
    with mock.patch("builtins.input", side_effect=EOFError):
        updated = _prompt_hosts(cfg, result)
    assert updated is None


def test_prompt_hosts_keyboard_interrupt(cfg: MailConfig) -> None:
    """KeyboardInterrupt during prompt → None returned."""
    result = _VerifyResult(imap_ok=False, smtp_ok=False)
    with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
        updated = _prompt_hosts(cfg, result)
    assert updated is None
