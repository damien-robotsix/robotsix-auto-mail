"""Shared helpers for draft-mixin and draft-generator unit tests.

Provides ``_patch_llm`` (patch the LLM provider to return a canned
``DraftResult``), ``_insert_inbox`` (insert an inbox ``MailRecord``
with sensible defaults), and ``_patch_smtp_and_imap`` (patch
SmtpClient and ImapClient together).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest import mock


@contextmanager
def _patch_smtp_and_imap() -> Iterator[tuple[mock.MagicMock, mock.MagicMock]]:
    with (
        mock.patch("robotsix_auto_mail.smtp.SmtpClient") as smtp_cls,
        mock.patch("robotsix_auto_mail.imap.ImapClient") as imap_cls,
    ):
        imap_client = imap_cls.return_value.__enter__.return_value
        imap_client.list_folders.return_value = [mock.Mock(delimiter="/")]
        yield smtp_cls, imap_cls


def _patch_llm(
    result_obj: "DraftResult",
) -> tuple[mock.MagicMock, mock._patch[mock.MagicMock]]:
    """Patch get_provider to return *result_obj* from the LLM.

    Returns the mock handle (to assert ``close()``) and the patcher.
    """

    mock_run_result = mock.MagicMock()
    mock_run_result.output = result_obj
    mock_handle = mock.MagicMock()
    mock_handle.run_sync.return_value = mock_run_result

    mock_provider = mock.MagicMock()
    mock_provider.build_agent.return_value = mock_handle
    mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

    patcher = mock.patch(
        "robotsix_llmio.core.factory.get_provider_for_identifier",
        return_value=mock_provider,
    )
    return mock_handle, patcher


def _insert_inbox(conn: object, message_id: str, **overrides: str) -> None:
    """Insert an inbox MailRecord with sensible defaults."""
    from robotsix_auto_mail.db import MailRecord, insert_record

    record = MailRecord(
        message_id=message_id,
        sender=overrides.get("sender", "alice@example.com"),
        subject=overrides.get("subject", "Hello"),
        date="2025-06-01T12:00:00",
        status=overrides.get("status", "to_read"),
        body_plain=overrides.get("body_plain", "Can we meet next week?"),
        notes=overrides.get("notes", ""),
    )
    insert_record(conn, record)  # type: ignore[arg-type]
