"""Shared test helpers for the pipeline test suite."""

from __future__ import annotations

from unittest import mock

from robotsix_auto_mail.imap import ImapClient


def _mock_imap_client() -> mock.MagicMock:
    """Return a MagicMock that looks enough like an ImapClient."""
    client = mock.MagicMock(spec=ImapClient)
    # select_folder_and_uidvalidity defaults to (count=1, uidvalidity=None) so
    # full-pipeline tests don't trigger a UIDVALIDITY reset unless they opt in.
    client.select_folder_and_uidvalidity.return_value = (1, None)
    return client


def _make_raw_message(
    *,
    message_id: str = "<abc123@example.com>",
    sender: str = "alice@example.com",
    subject: str = "Hello",
    date: str = "Wed, 15 Jan 2025 10:30:00 +0000",
    body: str = "plain text body",
) -> bytes:
    """Build a minimal, valid MIME message as bytes."""
    return (
        f"From: {sender}\r\n"
        f"To: bob@example.com\r\n"
        f"Subject: {subject}\r\n"
        f"Date: {date}\r\n"
        f"Message-ID: {message_id}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n"
        f"{body}"
    ).encode("utf-8")
