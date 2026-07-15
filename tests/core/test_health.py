"""Tests for the account health-check helpers (probe_account, utcnow)."""

from __future__ import annotations

from unittest import mock

from robotsix_auto_mail.core.health import probe_account, utcnow


def test_utcnow_returns_iso_format_no_microseconds() -> None:
    """``utcnow()`` returns an ISO-8601 string with no microseconds."""
    result = utcnow()
    # ISO-8601 with 'T' separator, no trailing microsecond component
    assert isinstance(result, str)
    assert "T" in result
    assert "." not in result  # no microseconds
    # Should end with a timezone offset (either +00:00 or Z) and no sub-second
    assert result.endswith("+00:00") or result[-1] == "Z"


def test_probe_account_both_succeed() -> None:
    """When both IMAP and SMTP connect without error, returns ``("ok", None)``."""
    with (
        mock.patch("robotsix_auto_mail.core.health.ImapClient") as mock_imap,
        mock.patch("robotsix_auto_mail.core.health.SmtpClient") as mock_smtp,
    ):
        config = mock.MagicMock()  # MailConfig -- don't need real fields
        status, error = probe_account(config)
        assert status == "ok"
        assert error is None
        mock_imap.assert_called_once_with(config)
        mock_smtp.assert_called_once_with(config)


def test_probe_account_imap_fails_smtp_succeeds() -> None:
    """IMAP raises ``ImapError``; SMTP succeeds — returns ``("failed", "IMAP: ...")``."""
    from robotsix_auto_mail.core.health import ImapError

    with (
        mock.patch("robotsix_auto_mail.core.health.ImapClient") as mock_imap,
        mock.patch("robotsix_auto_mail.core.health.SmtpClient") as mock_smtp,
    ):
        mock_imap.side_effect = ImapError("connection refused")
        config = mock.MagicMock()
        status, error = probe_account(config)
        assert status == "failed"
        assert error == "IMAP: connection refused"
        mock_imap.assert_called_once_with(config)
        mock_smtp.assert_called_once_with(config)  # SMTP still attempted


def test_probe_account_smtp_fails_imap_succeeds() -> None:
    """SMTP raises ``SmtpError``; IMAP succeeds — returns ``("failed", "SMTP: ...")``."""
    from robotsix_auto_mail.core.health import SmtpError

    with (
        mock.patch("robotsix_auto_mail.core.health.ImapClient") as mock_imap,
        mock.patch("robotsix_auto_mail.core.health.SmtpClient") as mock_smtp,
    ):
        mock_smtp.side_effect = SmtpError("auth failure")
        config = mock.MagicMock()
        status, error = probe_account(config)
        assert status == "failed"
        assert error == "SMTP: auth failure"
        mock_imap.assert_called_once_with(config)
        mock_smtp.assert_called_once_with(config)


def test_probe_account_both_fail() -> None:
    """Both IMAP and SMTP raise — error message aggregates both protocols."""
    from robotsix_auto_mail.core.health import ImapError, SmtpError

    with (
        mock.patch("robotsix_auto_mail.core.health.ImapClient") as mock_imap,
        mock.patch("robotsix_auto_mail.core.health.SmtpClient") as mock_smtp,
    ):
        mock_imap.side_effect = ImapError("timeout")
        mock_smtp.side_effect = SmtpError("connection refused")
        config = mock.MagicMock()
        status, error = probe_account(config)
        assert status == "failed"
        assert error == "IMAP: timeout; SMTP: connection refused"
        mock_imap.assert_called_once_with(config)
        mock_smtp.assert_called_once_with(config)
