"""Tests for SMTP client close() and context manager."""

from __future__ import annotations

import smtplib
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.smtp import SmtpClient
from tests.conftest import _make_mock_smtp, _make_mock_smtp_ssl

# ===================================================================
# close() tests
# ===================================================================


def test_close_calls_quit(cfg: MailConfig) -> None:
    """close() calls smtp.quit()."""
    mock_smtp = _make_mock_smtp()

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        client = SmtpClient(cfg)
        client.connect()
        client.close()

    mock_smtp.quit.assert_called_once()


def test_close_does_not_raise_if_already_disconnected(
    cfg: MailConfig,
) -> None:
    """close() swallows quit() failure (connection already closed)."""
    mock_smtp = _make_mock_smtp()
    mock_smtp.quit.side_effect = smtplib.SMTPException("already closed")

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        client = SmtpClient(cfg)
        client.connect()
        client.close()  # must not raise


def test_close_safe_to_call_multiple_times(cfg: MailConfig) -> None:
    """close() is safe to call multiple times."""
    mock_smtp = _make_mock_smtp()

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        client = SmtpClient(cfg)
        client.connect()
        client.close()
        client.close()  # second call is a no-op

    # quit() called exactly once
    mock_smtp.quit.assert_called_once()


def test_close_before_connect_does_nothing(cfg: MailConfig) -> None:
    """close() is a no-op if we never connected."""
    client = SmtpClient(cfg)
    client.close()  # must not raise


# ===================================================================
# Context manager tests
# ===================================================================


def test_context_manager_connects_on_enter_and_closes_on_exit(
    cfg: MailConfig,
) -> None:
    """__enter__ calls connect(), __exit__ calls close()."""
    mock_smtp = _make_mock_smtp()

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        with SmtpClient(cfg) as client:
            mock_smtp.login.assert_called_once()
            assert client is not None

    mock_smtp.quit.assert_called_once()


def test_context_manager_closes_on_exception(cfg: MailConfig) -> None:
    """quit() is called even when the block raises."""
    mock_smtp = _make_mock_smtp()

    with mock.patch("smtplib.SMTP", return_value=mock_smtp):
        try:
            with SmtpClient(cfg):
                raise RuntimeError("something went wrong inside the block")
        except RuntimeError:
            pass

    mock_smtp.quit.assert_called_once()


def test_context_manager_direct_tls_flow(cfg: MailConfig) -> None:
    """Direct-TLS context manager: lease → use → close."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_tls_mode="direct-tls",
        username="user@example.com",
        password="s3cret",
    )

    mock_smtp = _make_mock_smtp_ssl()

    with mock.patch("smtplib.SMTP_SSL", return_value=mock_smtp):
        with SmtpClient(cfg) as client:
            client.send(
                from_addr="bot@example.com",
                to_addr="user@example.com",
                subject="S",
                body="B",
            )

    mock_smtp.login.assert_called_once_with("user@example.com", "s3cret")
    mock_smtp.send_message.assert_called_once()
    mock_smtp.quit.assert_called_once()
