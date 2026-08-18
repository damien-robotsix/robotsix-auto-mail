"""Tests for setup_archive across first-run, subsequent-run, and failure paths."""

import json
from typing import cast
from unittest import mock

import pytest

from robotsix_auto_mail.db import get_watermark, init_db, set_watermark
from robotsix_auto_mail.db.archive import (
    _ARCHIVE_WATERMARK_KEY,
    ARCHIVE_ROOT,
    ArchiveError,
    ArchiveStructure,
    setup_archive,
)
from robotsix_auto_mail.imap import ImapClient, ImapError, MailboxInfo
from tests.db.conftest import _FakeImapClient, _folder, _patch_llm, _special_folder


# ---------------------------------------------------------------------------
# setup_archive — first run
# ---------------------------------------------------------------------------


def test_setup_archive_first_run_persists_without_creating() -> None:
    """First run persists the proposed layout but does not create IMAP folders."""
    conn = init_db(":memory:")
    try:
        client = _FakeImapClient([_folder("INBOX"), _folder("Sent")])
        with _patch_llm(["Receipts", "Work/2024"]):
            result = setup_archive(conn, cast(ImapClient, client), api_key="sk-test")

        expected = [
            ARCHIVE_ROOT,
            f"{ARCHIVE_ROOT}/Receipts",
            f"{ARCHIVE_ROOT}/Work/2024",
        ]
        assert result == expected
        # No IMAP folders should be created eagerly.
        assert client.created == []
        stored = get_watermark(conn, _ARCHIVE_WATERMARK_KEY)
        assert stored is not None
        assert json.loads(stored)["folders"] == expected
    finally:
        conn.close()


def test_setup_archive_translates_delimiter() -> None:
    """Sub-path separators are translated to the server delimiter in the watermark."""
    conn = init_db(":memory:")
    try:
        client = _FakeImapClient([_folder("INBOX", delimiter=".")])
        with _patch_llm(["Work/2024"]):
            result = setup_archive(conn, cast(ImapClient, client), api_key="sk-test")
        assert result == [ARCHIVE_ROOT, f"{ARCHIVE_ROOT}.Work.2024"]
        assert client.created == []
        stored = get_watermark(conn, _ARCHIVE_WATERMARK_KEY)
        assert stored is not None
        assert json.loads(stored)["folders"] == result
    finally:
        conn.close()


def test_setup_archive_skips_existing_folders() -> None:
    """Folders already present on the server are not recreated (never created eagerly)."""
    conn = init_db(":memory:")
    try:
        client = _FakeImapClient([_folder("INBOX"), _folder(ARCHIVE_ROOT)])
        with _patch_llm(["Receipts"]):
            result = setup_archive(conn, cast(ImapClient, client), api_key="sk-test")
        assert result == [ARCHIVE_ROOT, f"{ARCHIVE_ROOT}/Receipts"]
        # No folders are created eagerly — watermark is persisted only.
        assert client.created == []
    finally:
        conn.close()


def test_setup_archive_custom_root_persists_without_creating() -> None:
    """A custom archive_root is persisted without eager IMAP folder creation."""
    conn = init_db(":memory:")
    try:
        client = _FakeImapClient([_folder("INBOX"), _folder("Sent")])
        with _patch_llm(["Receipts", "Work/2024"]):
            result = setup_archive(
                conn,
                cast(ImapClient, client),
                archive_root="custom-archive",
                api_key="sk-test",
            )

        expected = [
            "custom-archive",
            "custom-archive/Receipts",
            "custom-archive/Work/2024",
        ]
        assert result == expected
        assert client.created == []
        # The default root is not used anywhere.
        assert ARCHIVE_ROOT not in result
        stored = get_watermark(conn, _ARCHIVE_WATERMARK_KEY)
        assert stored is not None
        assert json.loads(stored)["folders"] == expected
    finally:
        conn.close()


def test_setup_archive_custom_root_passed_to_llm() -> None:
    """The custom root is threaded into the LLM system prompt."""
    conn = init_db(":memory:")
    try:
        client = _FakeImapClient([_folder("INBOX")])
        with mock.patch(
            "robotsix_llmio.core.factory.get_provider_for_identifier"
        ) as cls:
            mock_run_result = mock.MagicMock()
            mock_run_result.output = ArchiveStructure(folders=[])
            mock_handle = mock.MagicMock()
            mock_handle.run_sync.return_value = mock_run_result
            provider = cls.return_value
            provider.build_agent.return_value = mock_handle
            provider.call_with_retry.side_effect = lambda fn, what: fn()

            setup_archive(
                conn,
                cast(ImapClient, client),
                archive_root="custom-archive",
                api_key="sk-test",
            )

        prompt = provider.build_agent.call_args.kwargs["system_prompt"]
        assert "custom-archive" in prompt
    finally:
        conn.close()


def test_setup_archive_excludes_special_use_folders_from_llm() -> None:
    """Gmail's special-use system folders are kept out of the LLM input."""
    conn = init_db(":memory:")
    try:
        client = _FakeImapClient(
            [
                _folder("INBOX"),
                _folder("Projects/acme"),
                _special_folder("[Gmail]", ("\\HasChildren", "\\Noselect")),
                _special_folder("[Gmail]/All Mail", ("\\All",)),
                _special_folder("[Gmail]/Sent Mail", ("\\Sent",)),
                _special_folder("[Gmail]/Trash", ("\\Trash",)),
                _special_folder("[Gmail]/Important", ("\\Important",)),
            ]
        )
        with mock.patch(
            "robotsix_llmio.core.factory.get_provider_for_identifier"
        ) as cls:
            mock_run_result = mock.MagicMock()
            mock_run_result.output = ArchiveStructure(folders=[])
            mock_handle = mock.MagicMock()
            mock_handle.run_sync.return_value = mock_run_result
            provider = cls.return_value
            provider.build_agent.return_value = mock_handle
            provider.call_with_retry.side_effect = lambda fn, what: fn()

            setup_archive(conn, cast(ImapClient, client), api_key="sk-test")

        user_message = mock_handle.run_sync.call_args.args[0]
        # Ordinary folders inform the layout; system folders are filtered out.
        assert "INBOX" in user_message
        assert "Projects/acme" in user_message
        assert "[Gmail]" not in user_message
        assert "All Mail" not in user_message
        assert "Trash" not in user_message
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# setup_archive — subsequent run
# ---------------------------------------------------------------------------


def test_setup_archive_subsequent_run_short_circuits() -> None:
    """Watermark present → no folder listing, no LLM, no create_folder."""
    conn = init_db(":memory:")
    try:
        persisted = [ARCHIVE_ROOT, f"{ARCHIVE_ROOT}/Receipts"]
        set_watermark(conn, _ARCHIVE_WATERMARK_KEY, json.dumps(persisted))
        client = mock.MagicMock()
        with mock.patch(
            "robotsix_llmio.core.factory.get_provider_for_identifier"
        ) as cls:
            result = setup_archive(conn, client)
        assert result == persisted
        client.list_folders.assert_not_called()
        client.create_folder.assert_not_called()
        cls.assert_not_called()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# setup_archive — no API key fallback
# ---------------------------------------------------------------------------


def test_setup_archive_no_api_key_falls_back_to_root() -> None:
    """Without an LLM key, only the root is persisted (no eager creation)."""
    conn = init_db(":memory:")
    try:
        client = _FakeImapClient([_folder("INBOX")])
        with mock.patch(
            "robotsix_llmio.core.factory.get_provider_for_identifier"
        ) as cls:
            result = setup_archive(conn, cast(ImapClient, client))
        assert result == [ARCHIVE_ROOT]
        assert client.created == []
        cls.assert_not_called()
        stored = get_watermark(conn, _ARCHIVE_WATERMARK_KEY)
        assert stored is not None
        assert json.loads(stored)["folders"] == [ARCHIVE_ROOT]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# setup_archive — IMAP failure paths must not persist a watermark
# ---------------------------------------------------------------------------


def test_setup_archive_llm_error_propagates_and_does_not_persist() -> None:
    """An LLM failure propagates and leaves no watermark."""
    conn = init_db(":memory:")
    try:
        client = _FakeImapClient([_folder("INBOX")])
        with mock.patch(
            "robotsix_llmio.core.factory.get_provider_for_identifier"
        ) as cls:
            mock_provider = mock.MagicMock()
            mock_handle = mock.MagicMock()
            mock_handle.run_sync.side_effect = RuntimeError("llm timeout")
            mock_provider.build_agent.return_value = mock_handle
            cls.return_value = mock_provider

            with pytest.raises(ArchiveError):
                setup_archive(conn, cast(ImapClient, client), api_key="sk-test")
        assert get_watermark(conn, _ARCHIVE_WATERMARK_KEY) is None
    finally:
        conn.close()


def test_setup_archive_list_folders_error_propagates_and_does_not_persist() -> None:
    """A list_folders ImapError propagates and leaves no watermark."""
    conn = init_db(":memory:")
    try:

        class _FailingListClient(_FakeImapClient):
            def list_folders(self) -> list[MailboxInfo]:
                raise ImapError("LIST failed")

        client = _FailingListClient([])
        with pytest.raises(ImapError):
            setup_archive(conn, cast(ImapClient, client), api_key="sk-test")
        assert get_watermark(conn, _ARCHIVE_WATERMARK_KEY) is None
    finally:
        conn.close()
