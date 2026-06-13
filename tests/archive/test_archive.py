"""Tests for the self-managed archive folder structure subsystem."""

from __future__ import annotations

import json
import os
from typing import cast
from unittest import mock

import pytest
from robotsix_llmio.core import Tier

from robotsix_auto_mail._constants import _ARCHIVE_TAXONOMY_GUIDANCE
from robotsix_auto_mail.archive import (
    _ARCHIVE_WATERMARK_KEY,
    ARCHIVE_ROOT,
    ArchiveError,
    ArchiveStructure,
    _build_archive_system_prompt,
    determine_archive_structure,
    setup_archive,
)
from robotsix_auto_mail.db import get_watermark, init_db, set_watermark
from robotsix_auto_mail.imap import ImapClient, ImapError, MailboxInfo


class _FakeImapClient:
    """Minimal stand-in exposing list_folders() and create_folder()."""

    def __init__(self, folders: list[MailboxInfo]) -> None:
        self._folders = folders
        self.created: list[str] = []

    def list_folders(self) -> list[MailboxInfo]:
        return self._folders

    def create_folder(self, name: str) -> None:
        self.created.append(name)


def _folder(name: str, delimiter: str = "/") -> MailboxInfo:
    return MailboxInfo(name=name, attributes=(), delimiter=delimiter)


def _special_folder(
    name: str, attributes: tuple[str, ...], delimiter: str = "/"
) -> MailboxInfo:
    return MailboxInfo(name=name, attributes=attributes, delimiter=delimiter)


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
        "robotsix_llmio.core.get_provider",
        return_value=mock_provider,
    )


# ---------------------------------------------------------------------------
# ArchiveStructure
# ---------------------------------------------------------------------------


def test_archive_structure_defaults_empty() -> None:
    """folders defaults to an empty list."""
    assert ArchiveStructure().folders == []


def test_archive_structure_accepts_folders() -> None:
    """folders is populated from input."""
    s = ArchiveStructure(folders=["a", "a/b"])
    assert s.folders == ["a", "a/b"]


# ---------------------------------------------------------------------------
# ArchiveError
# ---------------------------------------------------------------------------


def test_archive_error_is_exception() -> None:
    err = ArchiveError("boom")
    assert isinstance(err, Exception)
    assert str(err) == "boom"


# ---------------------------------------------------------------------------
# Lazy provider import — deterministic path must not bind the extra
# ---------------------------------------------------------------------------


def test_provider_not_bound_at_module_level() -> None:
    """Importing the module must not require a concrete provider extra.

    The provider is resolved lazily inside ``determine_archive_structure``,
    so it must not be a module-level attribute of ``archive``.
    """
    import robotsix_auto_mail.archive as archive_mod

    assert not hasattr(archive_mod, "get_provider")


# ---------------------------------------------------------------------------
# determine_archive_structure
# ---------------------------------------------------------------------------


def test_determine_archive_structure_success() -> None:
    """The model's relative sub-paths are returned."""
    with mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}, clear=True):
        with _patch_llm(["Receipts", "Work/2024"]):
            result = determine_archive_structure(["INBOX", "Sent"])
    assert result == ["Receipts", "Work/2024"]


def test_determine_archive_structure_uses_cheap_tier() -> None:
    """build_agent is called with Tier.CHEAP by default."""
    with mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}, clear=True):
        with mock.patch("robotsix_llmio.core.get_provider") as cls:
            mock_run_result = mock.MagicMock()
            mock_run_result.output = ArchiveStructure(folders=[])
            mock_handle = mock.MagicMock()
            mock_handle.run_sync.return_value = mock_run_result
            provider = cls.return_value
            provider.build_agent.return_value = mock_handle
            provider.call_with_retry.side_effect = lambda fn, what: fn()

            determine_archive_structure(["INBOX"])

        provider.build_agent.assert_called_once()
        assert provider.build_agent.call_args.kwargs["tier"] == Tier.CHEAP
        mock_handle.close.assert_called_once()


def test_determine_archive_structure_missing_api_key() -> None:
    """No api_key and no LLM_API_KEY env var → ArchiveError."""
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ArchiveError) as exc:
            determine_archive_structure(["INBOX"])
    assert "LLM_API_KEY" in str(exc.value)


def test_determine_archive_structure_llm_error_wrapped() -> None:
    """A call_with_retry failure is wrapped in ArchiveError."""
    with mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}, clear=True):
        mock_handle = mock.MagicMock()
        mock_provider = mock.MagicMock()
        mock_provider.build_agent.return_value = mock_handle
        mock_handle.run_sync.side_effect = RuntimeError("timeout")
        with mock.patch(
            "robotsix_llmio.core.get_provider",
            return_value=mock_provider,
        ):
            with pytest.raises(ArchiveError) as exc:
                determine_archive_structure(["INBOX"])
    assert "timeout" in str(exc.value)
    mock_handle.close.assert_called_once()


# ---------------------------------------------------------------------------
# setup_archive — first run
# ---------------------------------------------------------------------------


def test_setup_archive_first_run_creates_and_persists() -> None:
    """First run lists folders, creates archive folders, persists, returns."""
    conn = init_db(":memory:")
    try:
        client = _FakeImapClient([_folder("INBOX"), _folder("Sent")])
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}, clear=True):
            with _patch_llm(["Receipts", "Work/2024"]):
                result = setup_archive(conn, cast(ImapClient, client))

        expected = [
            ARCHIVE_ROOT,
            f"{ARCHIVE_ROOT}/Receipts",
            f"{ARCHIVE_ROOT}/Work/2024",
        ]
        assert result == expected
        assert client.created == expected
        stored = get_watermark(conn, _ARCHIVE_WATERMARK_KEY)
        assert stored is not None
        assert json.loads(stored)["folders"] == expected
    finally:
        conn.close()


def test_setup_archive_translates_delimiter() -> None:
    """Sub-path separators are translated to the server delimiter."""
    conn = init_db(":memory:")
    try:
        client = _FakeImapClient([_folder("INBOX", delimiter=".")])
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}, clear=True):
            with _patch_llm(["Work/2024"]):
                result = setup_archive(conn, cast(ImapClient, client))
        assert result == [ARCHIVE_ROOT, f"{ARCHIVE_ROOT}.Work.2024"]
        assert client.created == result
    finally:
        conn.close()


def test_setup_archive_skips_existing_folders() -> None:
    """Folders already present on the server are not recreated."""
    conn = init_db(":memory:")
    try:
        client = _FakeImapClient([_folder("INBOX"), _folder(ARCHIVE_ROOT)])
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}, clear=True):
            with _patch_llm(["Receipts"]):
                result = setup_archive(conn, cast(ImapClient, client))
        assert result == [ARCHIVE_ROOT, f"{ARCHIVE_ROOT}/Receipts"]
        # ARCHIVE_ROOT already existed → only the sub-folder is created.
        assert client.created == [f"{ARCHIVE_ROOT}/Receipts"]
    finally:
        conn.close()


def test_setup_archive_custom_root_creates_and_persists() -> None:
    """A custom archive_root is used for created/persisted folder names."""
    conn = init_db(":memory:")
    try:
        client = _FakeImapClient([_folder("INBOX"), _folder("Sent")])
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}, clear=True):
            with _patch_llm(["Receipts", "Work/2024"]):
                result = setup_archive(
                    conn,
                    cast(ImapClient, client),
                    archive_root="custom-archive",
                )

        expected = [
            "custom-archive",
            "custom-archive/Receipts",
            "custom-archive/Work/2024",
        ]
        assert result == expected
        assert client.created == expected
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
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}, clear=True):
            with mock.patch("robotsix_llmio.core.get_provider") as cls:
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
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}, clear=True):
            with mock.patch("robotsix_llmio.core.get_provider") as cls:
                mock_run_result = mock.MagicMock()
                mock_run_result.output = ArchiveStructure(folders=[])
                mock_handle = mock.MagicMock()
                mock_handle.run_sync.return_value = mock_run_result
                provider = cls.return_value
                provider.build_agent.return_value = mock_handle
                provider.call_with_retry.side_effect = lambda fn, what: fn()

                setup_archive(conn, cast(ImapClient, client))

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
        with mock.patch("robotsix_llmio.core.get_provider") as cls:
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
    """Without an LLM key, only the root is created and persisted."""
    conn = init_db(":memory:")
    try:
        client = _FakeImapClient([_folder("INBOX")])
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("robotsix_llmio.core.get_provider") as cls:
                result = setup_archive(conn, cast(ImapClient, client))
        assert result == [ARCHIVE_ROOT]
        assert client.created == [ARCHIVE_ROOT]
        cls.assert_not_called()
        stored = get_watermark(conn, _ARCHIVE_WATERMARK_KEY)
        assert stored is not None
        assert json.loads(stored)["folders"] == [ARCHIVE_ROOT]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# setup_archive — IMAP failure paths must not persist a watermark
# ---------------------------------------------------------------------------


def test_setup_archive_create_folder_error_propagates_and_does_not_persist() -> None:
    """A create_folder ImapError propagates and leaves no watermark."""
    conn = init_db(":memory:")
    try:

        class _FailingCreateClient(_FakeImapClient):
            def create_folder(self, name: str) -> None:
                raise ImapError("CREATE failed: NO")

        client = _FailingCreateClient([_folder("INBOX")])
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}, clear=True):
            with _patch_llm(["Receipts"]):
                with pytest.raises(ImapError):
                    setup_archive(conn, cast(ImapClient, client))
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
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}, clear=True):
            with pytest.raises(ImapError):
                setup_archive(conn, cast(ImapClient, client))
        assert get_watermark(conn, _ARCHIVE_WATERMARK_KEY) is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# setup_archive — namespace prefix
# ---------------------------------------------------------------------------


def test_setup_archive_namespace_prepends_to_root() -> None:
    """archive_namespace is prepended to archive_root for folder names."""
    conn = init_db(":memory:")
    try:
        client = _FakeImapClient([_folder("INBOX"), _folder("Sent")])
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}, clear=True):
            with _patch_llm(["Receipts"]):
                result = setup_archive(
                    conn,
                    cast(ImapClient, client),
                    archive_root="robotsix-mail-archive",
                    archive_namespace="INBOX.",
                )

        expected = [
            "INBOX.robotsix-mail-archive",
            "INBOX.robotsix-mail-archive/Receipts",
        ]
        assert result == expected
        assert client.created == expected
        stored = get_watermark(conn, _ARCHIVE_WATERMARK_KEY)
        assert stored is not None
        assert json.loads(stored)["folders"] == expected
    finally:
        conn.close()


def test_setup_archive_namespace_llm_sees_original_root() -> None:
    """The LLM prompt receives the original archive_root, not the
    namespaced version."""
    conn = init_db(":memory:")
    try:
        client = _FakeImapClient([_folder("INBOX")])
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}, clear=True):
            with mock.patch("robotsix_llmio.core.get_provider") as cls:
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
                    archive_root="my-archive",
                    archive_namespace="INBOX.",
                )

        prompt = provider.build_agent.call_args.kwargs["system_prompt"]
        # The LLM should see the logical root, not the namespaced one.
        assert "my-archive" in prompt
        assert "INBOX.my-archive" not in prompt
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Prompt content — taxonomy guidance
# ---------------------------------------------------------------------------


def test_archive_structure_prompt_includes_taxonomy_guidance() -> None:
    """The structure-proposal prompt includes the shared taxonomy guidance."""
    prompt = _build_archive_system_prompt("robotsix-mail-archive")
    lower = prompt.lower()
    assert "purpose" in lower
    assert "topic" in lower
    assert "do not use bare" in lower
    assert "domain" in lower
    assert "sender" in lower
    assert "at most 2 levels" in prompt


def test_archive_structure_prompt_legacy_folders_guidance() -> None:
    """The structure prompt warns against propagating legacy domain/sender patterns."""
    prompt = _build_archive_system_prompt("robotsix-mail-archive")
    assert "legacy" in prompt.lower()
    assert "re-home" in prompt.lower() or "do not propagate" in prompt.lower()


def test_archive_and_triage_prompts_share_taxonomy() -> None:
    """Both prompts embed the exact same _ARCHIVE_TAXONOMY_GUIDANCE string."""
    from robotsix_auto_mail.triage import _build_triage_system_prompt

    archive_prompt = _build_archive_system_prompt("root")
    triage_prompt = _build_triage_system_prompt(archive_folders=["Newsletters/LWN"])
    assert _ARCHIVE_TAXONOMY_GUIDANCE in archive_prompt
    assert _ARCHIVE_TAXONOMY_GUIDANCE in triage_prompt
