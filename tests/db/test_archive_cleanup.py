"""Tests for cleanup_empty_archive_folders."""

from typing import cast

from robotsix_auto_mail.db.archive import ARCHIVE_ROOT, cleanup_empty_archive_folders
from robotsix_auto_mail.imap import ImapClient, MailboxInfo
from tests.db.conftest import _folder


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


# ---------------------------------------------------------------------------
# cleanup_empty_archive_folders
# ---------------------------------------------------------------------------


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
    deleted, _skipped = cleanup_empty_archive_folders(
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
    deleted, _skipped = cleanup_empty_archive_folders(
        cast(ImapClient, client),
        archive_root=root,
    )
    assert deleted == 1
    assert client.deleted == [f"{root}/Empty"]
