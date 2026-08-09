"""Tests for the self-managed archive folder structure subsystem."""

from __future__ import annotations

import json
from typing import cast
from unittest import mock

import pytest

from robotsix_auto_mail.core._constants import _ARCHIVE_TAXONOMY_GUIDANCE
from robotsix_auto_mail.db import get_watermark, init_db, set_watermark
from robotsix_auto_mail.db.archive import (
    _ARCHIVE_WATERMARK_KEY,
    ARCHIVE_ROOT,
    ArchiveError,
    ArchiveStructure,
    _build_archive_system_prompt,
    cleanup_empty_archive_folders,
    determine_archive_structure,
    setup_archive,
)
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
        "robotsix_llmio.core.factory.get_provider_for_identifier",
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
    import robotsix_auto_mail.db.archive as archive_mod

    assert not hasattr(archive_mod, "get_provider_for_identifier")


# ---------------------------------------------------------------------------
# determine_archive_structure
# ---------------------------------------------------------------------------


def test_determine_archive_structure_success() -> None:
    """The model's relative sub-paths are returned."""
    with _patch_llm(["Receipts", "Work/2024"]):
        result = determine_archive_structure(["INBOX", "Sent"], api_key="sk-test")
    assert result == ["Receipts", "Work/2024"]


def test_determine_archive_structure_uses_cheap_tier() -> None:
    """build_agent is called with level=1 (cheap) by default."""
    with mock.patch("robotsix_llmio.core.factory.get_provider_for_identifier") as cls:
        mock_run_result = mock.MagicMock()
        mock_run_result.output = ArchiveStructure(folders=[])
        mock_handle = mock.MagicMock()
        mock_handle.run_sync.return_value = mock_run_result
        provider = cls.return_value
        provider.build_agent.return_value = mock_handle
        provider.call_with_retry.side_effect = lambda fn, what: fn()

        determine_archive_structure(["INBOX"], api_key="sk-test")

    provider.build_agent.assert_called_once()
    assert provider.build_agent.call_args.kwargs["level"] == 1
    mock_handle.close.assert_called_once()


def test_determine_archive_structure_missing_api_key() -> None:
    """No api_key and no LLM_API_KEY env var → ArchiveError."""
    with pytest.raises(ArchiveError) as exc:
        determine_archive_structure(["INBOX"])
    assert "openrouter.keys" in str(exc.value)


def test_determine_archive_structure_llm_error_wrapped() -> None:
    """A call_with_retry failure is wrapped in ArchiveError."""
    mock_handle = mock.MagicMock()
    mock_provider = mock.MagicMock()
    mock_provider.build_agent.return_value = mock_handle
    mock_handle.run_sync.side_effect = RuntimeError("timeout")
    with mock.patch(
        "robotsix_llmio.core.factory.get_provider_for_identifier",
        return_value=mock_provider,
    ):
        with pytest.raises(ArchiveError) as exc:
            determine_archive_structure(["INBOX"], api_key="sk-test")
    assert "timeout" in str(exc.value)
    mock_handle.close.assert_called_once()


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


# ---------------------------------------------------------------------------
# cleanup_empty_archive_folders
# ---------------------------------------------------------------------------


class _FakeCleanupClient:
    """Fake IMAP client for testing cleanup_empty_archive_folders.

    Supports list_folders(), select_folder() → message count, and
    delete_folder().
    """

    def __init__(
        self,
        folders: list[MailboxInfo],
        message_counts: dict[str, int] | None = None,
    ) -> None:
        self._folders = folders
        self._message_counts: dict[str, int] = dict(message_counts or {})
        self.deleted: list[str] = []

    def list_folders(self) -> list[MailboxInfo]:
        return self._folders

    def select_folder(self, name: str) -> int:
        return self._message_counts.get(name, 0)

    def delete_folder(self, name: str) -> None:
        self.deleted.append(name)


def test_cleanup_empty_archive_folders_no_archive_folders() -> None:
    """No folders under archive root → nothing deleted."""
    client = _FakeCleanupClient(
        [_folder("INBOX"), _folder("Sent")],
    )
    deleted, skipped = cleanup_empty_archive_folders(
        cast(ImapClient, client),
        archive_root=ARCHIVE_ROOT,
    )
    assert deleted == 0
    assert skipped == 0
    assert client.deleted == []


def test_cleanup_empty_archive_folders_only_root_exists() -> None:
    """Only the root folder → nothing deleted, root is skipped."""
    client = _FakeCleanupClient(
        [_folder(ARCHIVE_ROOT)],
    )
    deleted, skipped = cleanup_empty_archive_folders(
        cast(ImapClient, client),
        archive_root=ARCHIVE_ROOT,
    )
    assert deleted == 0
    assert skipped == 0
    assert client.deleted == []


def test_cleanup_empty_archive_folders_deletes_empty_leaves() -> None:
    """Empty leaf folders are deleted."""
    client = _FakeCleanupClient(
        [
            _folder(ARCHIVE_ROOT),
            _folder(f"{ARCHIVE_ROOT}/Empty"),
            _folder(f"{ARCHIVE_ROOT}/NotEmpty"),
        ],
        message_counts={
            ARCHIVE_ROOT: 5,
            f"{ARCHIVE_ROOT}/Empty": 0,
            f"{ARCHIVE_ROOT}/NotEmpty": 3,
        },
    )
    deleted, skipped = cleanup_empty_archive_folders(
        cast(ImapClient, client),
        archive_root=ARCHIVE_ROOT,
    )
    assert deleted == 1
    assert skipped == 1
    assert client.deleted == [f"{ARCHIVE_ROOT}/Empty"]


def test_cleanup_empty_archive_folders_keeps_parents_with_children() -> None:
    """A parent folder with non-empty children is kept."""
    client = _FakeCleanupClient(
        [
            _folder(ARCHIVE_ROOT),
            _folder(f"{ARCHIVE_ROOT}/Parent"),
            _folder(f"{ARCHIVE_ROOT}/Parent/NotEmpty"),
        ],
        message_counts={
            ARCHIVE_ROOT: 0,
            f"{ARCHIVE_ROOT}/Parent": 0,
            f"{ARCHIVE_ROOT}/Parent/NotEmpty": 5,
        },
    )
    deleted, skipped = cleanup_empty_archive_folders(
        cast(ImapClient, client),
        archive_root=ARCHIVE_ROOT,
    )
    # Parent/NotEmpty has messages → kept, so Parent is also kept
    # (has non-empty child).
    assert deleted == 0
    assert skipped == 2
    assert client.deleted == []


def test_cleanup_empty_archive_folders_deletes_cascade() -> None:
    """Empty parent folders are deleted after their empty children are removed."""
    client = _FakeCleanupClient(
        [
            _folder(ARCHIVE_ROOT),
            _folder(f"{ARCHIVE_ROOT}/Parent"),
            _folder(f"{ARCHIVE_ROOT}/Parent/Empty"),
        ],
        message_counts={
            ARCHIVE_ROOT: 0,
            f"{ARCHIVE_ROOT}/Parent": 0,
            f"{ARCHIVE_ROOT}/Parent/Empty": 0,
        },
    )
    deleted, skipped = cleanup_empty_archive_folders(
        cast(ImapClient, client),
        archive_root=ARCHIVE_ROOT,
    )
    # Empty leaf deleted → Parent becomes childless and empty → deleted too.
    # Root is never deleted (excluded from by_depth).
    assert deleted == 2
    assert skipped == 0
    assert client.deleted == [
        f"{ARCHIVE_ROOT}/Parent/Empty",
        f"{ARCHIVE_ROOT}/Parent",
    ]


def test_cleanup_empty_archive_folders_never_deletes_root() -> None:
    """The archive root is never deleted even if empty."""
    client = _FakeCleanupClient(
        [
            _folder(ARCHIVE_ROOT),
        ],
        message_counts={
            ARCHIVE_ROOT: 0,
        },
    )
    deleted, skipped = cleanup_empty_archive_folders(
        cast(ImapClient, client),
        archive_root=ARCHIVE_ROOT,
    )
    assert deleted == 0
    assert ARCHIVE_ROOT not in client.deleted


def test_cleanup_empty_archive_folders_custom_root() -> None:
    """Works with a custom archive root."""
    root = "my-archive"
    client = _FakeCleanupClient(
        [
            _folder(root),
            _folder(f"{root}/Empty"),
        ],
        message_counts={
            root: 3,
            f"{root}/Empty": 0,
        },
    )
    deleted, skipped = cleanup_empty_archive_folders(
        cast(ImapClient, client),
        archive_root=root,
    )
    assert deleted == 1
    assert client.deleted == [f"{root}/Empty"]
