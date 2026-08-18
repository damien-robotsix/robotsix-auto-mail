"""Shared (non-fixture) helpers for the archive subsystem test modules."""

from __future__ import annotations

from unittest import mock

from robotsix_auto_mail.db.archive import ArchiveStructure
from robotsix_auto_mail.imap import MailboxInfo


def _folder(name: str, delimiter: str = "/") -> MailboxInfo:
    return MailboxInfo(name=name, attributes=(), delimiter=delimiter)


def _special_folder(
    name: str, attributes: tuple[str, ...], delimiter: str = "/"
) -> MailboxInfo:
    return MailboxInfo(name=name, attributes=attributes, delimiter=delimiter)


class _FakeImapClient:
    """Minimal stand-in exposing list_folders() and create_folder()."""

    def __init__(self, folders: list[MailboxInfo]) -> None:
        self._folders = folders
        self.created: list[str] = []

    def list_folders(self) -> list[MailboxInfo]:
        return self._folders

    def create_folder(self, name: str) -> None:
        self.created.append(name)


def _patch_llm(folders: list[str]) -> mock._patch[mock.MagicMock]:
    """Patch get_provider to return *folders* from the LLM."""
    mock_run_result = mock.MagicMock()
    mock_run_result.output = ArchiveStructure(folders=folders)
    mock_handle = mock.MagicMock()
    mock_handle.run_sync.return_value = mock_run_result

    mock_provider = mock.MagicMock()
    mock_provider.build_agent.return_value = mock_handle
    mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

    return mock.patch(
        "robotsix_llmio.core.factory.get_provider_for_identifier",
        return_value=mock_provider,
    )
