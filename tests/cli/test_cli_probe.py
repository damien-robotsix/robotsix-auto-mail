"""Tests for the CLI probe subcommand."""

from __future__ import annotations

import imaplib
import smtplib
import ssl
from unittest import mock

import pytest
from tests.conftest import _make_mock_imap_ssl, _make_mock_smtp

from robotsix_auto_mail.cli import main
from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig


def _accounts(cfg: MailConfig, account_id: str = "default") -> MailAccountsConfig:
    """Wrap a single ``MailConfig`` in a one-element accounts container."""
    return MailAccountsConfig(
        accounts=(MailAccount(account_id=account_id, config=cfg, label=None),),
        default_account_id=account_id,
    )


# ---------------------------------------------------------------------------
# probe - success
# ---------------------------------------------------------------------------


def test_probe_success(cfg: MailConfig, capsys: pytest.CaptureFixture[str]) -> None:
    """probe exits 0 and prints IMAP + SMTP metadata when both succeed."""
    mock_imap = _make_mock_imap_ssl()
    mock_smtp = _make_mock_smtp()

    with (
        mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 0
    captured = capsys.readouterr()
    out, err = captured.out, captured.err

    # IMAP output
    assert "IMAP Probe" in out
    assert "* OK IMAP4 ready" in out
    assert "IMAP4rev1" in out
    assert "INBOX" in out
    assert "[Gmail]" in out

    # SMTP output
    assert "SMTP Probe" in out
    assert "250-smtp.example.com" in out
    assert "STARTTLS" in out
    assert "AUTH" in out

    # No errors on stderr
    assert err == ""


# ---------------------------------------------------------------------------
# probe - IMAP failure, SMTP succeeds
# ---------------------------------------------------------------------------


def test_probe_imap_failure_smtp_ok(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """When IMAP fails, SMTP is still probed and exit code is 1."""
    mock_imap = mock.MagicMock(spec=imaplib.IMAP4_SSL)
    mock_imap.login.side_effect = imaplib.IMAP4.error("AUTHENTICATIONFAILED")
    mock_imap.sock = mock.MagicMock()

    mock_smtp = _make_mock_smtp()

    with (
        mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 1
    captured = capsys.readouterr()
    out, err = captured.out, captured.err

    # SMTP probe still ran
    assert "SMTP Probe" in out
    assert "250-smtp.example.com" in out

    # IMAP error on stderr
    assert "Error:" in err
    assert "AUTHENTICATIONFAILED" in err


# ---------------------------------------------------------------------------
# probe - SMTP failure, IMAP succeeds
# ---------------------------------------------------------------------------


def test_probe_smtp_failure_imap_ok(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """When SMTP fails, IMAP is still probed and exit code is 1."""
    mock_imap = _make_mock_imap_ssl()
    mock_smtp = mock.MagicMock(spec=smtplib.SMTP)
    mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(
        535, b"5.7.8 Authentication failed"
    )

    with (
        mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 1
    captured = capsys.readouterr()
    out, err = captured.out, captured.err

    # IMAP probe still ran
    assert "IMAP Probe" in out
    assert "INBOX" in out

    # SMTP error on stderr
    assert "Error:" in err
    assert "Authentication failed" in err


# ---------------------------------------------------------------------------
# probe - both fail
# ---------------------------------------------------------------------------


def test_probe_both_fail(cfg: MailConfig, capsys: pytest.CaptureFixture[str]) -> None:
    """When both fail, exit code is 1 and both errors are reported."""
    mock_imap = mock.MagicMock(spec=imaplib.IMAP4_SSL)
    mock_imap.login.side_effect = imaplib.IMAP4.error("BAD")
    mock_imap.sock = mock.MagicMock()

    mock_smtp = mock.MagicMock(spec=smtplib.SMTP)
    mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")

    with (
        mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 1
    err = capsys.readouterr().err
    # Both errors reported
    assert err.count("Error:") == 2


# ---------------------------------------------------------------------------
# probe - never calls send_message
# ---------------------------------------------------------------------------


def test_probe_never_calls_send_message(
    cfg: MailConfig,
) -> None:
    """The SMTP mock's send_message is never called."""
    mock_imap = _make_mock_imap_ssl()
    mock_smtp = _make_mock_smtp()

    with (
        mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        main(["probe"])

    mock_smtp.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# probe - connection refusal for IMAP
# ---------------------------------------------------------------------------


def test_probe_imap_connection_refused(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """probe handles IMAP connection-refused gracefully."""
    mock_smtp = _make_mock_smtp()

    with (
        mock.patch(
            "imaplib.IMAP4_SSL",
            side_effect=ConnectionRefusedError("Connection refused"),
        ),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Error:" in err
    assert "Connection refused" in err


# ---------------------------------------------------------------------------
# probe - connection refusal for SMTP
# ---------------------------------------------------------------------------


def test_probe_smtp_connection_refused(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """probe handles SMTP connection-refused gracefully."""
    mock_imap = _make_mock_imap_ssl()

    with (
        mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        mock.patch(
            "smtplib.SMTP",
            side_effect=ConnectionRefusedError("Connection refused"),
        ),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Error:" in err
    assert "Connection refused" in err


# ---------------------------------------------------------------------------
# probe - TLS failure for IMAP
# ---------------------------------------------------------------------------


def test_probe_imap_tls_failure(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """probe handles IMAP TLS failure gracefully (for STARTTLS)."""
    # Use a config with starttls so we can inject a TLS error
    cfg = MailConfig(
        imap_host="imap.example.com",
        imap_port=143,
        imap_tls_mode="starttls",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_tls_mode="starttls",
        username="user@example.com",
        password="s3cret",
    )

    mock_imap = mock.MagicMock(spec=imaplib.IMAP4)
    mock_imap.starttls.side_effect = ssl.SSLError("handshake failed")
    mock_imap.sock = mock.MagicMock()

    mock_smtp = _make_mock_smtp()

    with (
        mock.patch("imaplib.IMAP4", return_value=mock_imap),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Error:" in err
    assert "handshake" in err.lower()


# ---------------------------------------------------------------------------
# probe - SMTP STARTTLS failure
# ---------------------------------------------------------------------------


def test_probe_smtp_tls_failure(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """probe handles SMTP TLS failure gracefully."""
    mock_imap = _make_mock_imap_ssl()
    mock_smtp = mock.MagicMock(spec=smtplib.SMTP)
    mock_smtp.ehlo_or_helo_if_needed.return_value = (250, b"OK")
    mock_smtp.starttls.side_effect = ssl.SSLError("certificate verify failed")

    with (
        mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Error:" in err
    assert "STARTTLS" in err or "certificate" in err


# ---------------------------------------------------------------------------
# probe - IMAP authentication failure
# ---------------------------------------------------------------------------


def test_probe_imap_auth_failure(
    cfg: MailConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """probe handles IMAP authentication failure gracefully."""
    mock_imap = mock.MagicMock(spec=imaplib.IMAP4_SSL)
    mock_imap.login.side_effect = imaplib.IMAP4.error(
        "AUTHENTICATIONFAILED invalid credentials"
    )
    mock_imap.sock = mock.MagicMock()

    mock_smtp = _make_mock_smtp()

    with (
        mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        mock.patch("smtplib.SMTP", return_value=mock_smtp),
        mock.patch("robotsix_auto_mail.config.MailConfig.from_env", return_value=cfg),
    ):
        rc = main(["probe"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Error:" in err
    assert "Authentication failed" in err


# ---------------------------------------------------------------------------
# Config loading failure
# ---------------------------------------------------------------------------


def test_probe_config_load_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """probe exits with code 1 when config loading fails."""
    with mock.patch(
        "robotsix_auto_mail.config.MailConfig.from_env",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(SystemExit) as exc:
            main(["probe"])

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Error loading configuration" in err
    assert "boom" in err
