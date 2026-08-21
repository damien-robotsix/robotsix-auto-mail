"""Tests for ``_BoardActionMixin._handle_archive_delete``.

Verifies POST /archive-delete behaviour including force-delete with
child subfolders (deepest-first recursive deletion), force-delete of
leaf folders, and non-force rejection of non-empty / child-bearing
folders.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from tests.server._test_helpers import _FakeHandler


def _make_handler(db_path: str, mail_config: MailConfig) -> _FakeHandler:
    """Create a ``_FakeHandler`` with ``_serve_json`` mocked."""
    handler = _FakeHandler(db_path, mail_config=mail_config)
    handler._serve_json = mock.MagicMock()
    return handler


def _make_folder(name: str, delimiter: str = "/") -> mock.Mock:
    """Build a mock FolderInfo with *name* and *delimiter*."""
    f = mock.Mock()
    f.name = name
    f.delimiter = delimiter
    return f


def _make_archive_delete_request(
    handler: _FakeHandler,
    *,
    source_folder: str,
    confirm: bool = True,
    force: bool = False,
) -> None:
    """Set up *handler* so ``_handle_archive_delete`` reads a valid JSON body."""
    body = json.dumps(
        {
            "source_folder": source_folder,
            "confirm": confirm,
            "force": force,
        }
    ).encode("utf-8")
    handler.headers.get.return_value = len(body)
    handler.rfile.read.return_value = body


@pytest.fixture(autouse=True)
def record_user_action_mock() -> Iterator[mock.MagicMock]:
    """Patch ``record_user_action`` so no background flash-LLM thread runs."""
    with mock.patch(
        "robotsix_auto_mail.server._archive_action_mixin.record_user_action"
    ) as patched:
        yield patched


class TestHandleArchiveDeleteForce:
    """Force-delete tests for ``_handle_archive_delete``."""

    def test_force_delete_leaf_folder(self, tmp_db_path: str) -> None:
        """Force-delete of a leaf folder (no children) expunges messages
        then deletes the folder."""
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="Archive",
        )
        handler = _make_handler(tmp_db_path, mail_config)
        _make_archive_delete_request(handler, source_folder="Newsletters", force=True)

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [
                _make_folder("Archive"),
                _make_folder("Archive/Newsletters"),
            ]
            mock_client.search_uids.return_value = [1, 2, 3]

            handler._handle_archive_delete()

        # Should select, expunge, then delete only the target folder.
        assert mock_client.select_folder.call_args_list == [
            mock.call("Archive/Newsletters"),
        ]
        mock_client.search_uids.assert_called_once_with("ALL")
        mock_client.delete_messages.assert_called_once_with([1, 2, 3])
        mock_client.delete_folder.assert_called_once_with("Archive/Newsletters")
        handler._serve_json.assert_called_once_with(
            {"status": "deleted", "source_folder": "Newsletters"}
        )

    def test_force_delete_with_child_subfolders_deepest_first(
        self, tmp_db_path: str
    ) -> None:
        """Force-delete of a folder with child subfolders deletes children
        deepest-first, then the target folder."""
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="Archive",
        )
        handler = _make_handler(tmp_db_path, mail_config)
        _make_archive_delete_request(handler, source_folder="Newsletters", force=True)

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [
                _make_folder("Archive"),
                _make_folder("Archive/Newsletters"),
                _make_folder("Archive/Newsletters/2024"),
                _make_folder("Archive/Newsletters/2024/Q1"),
                _make_folder("Archive/Newsletters/2025"),
            ]
            # Return some UIDs for every search.
            mock_client.search_uids.side_effect = [
                [10],  # Q1 (deepest)
                [11],  # 2024
                [12],  # 2025
                [1, 2, 3],  # Newsletters (target)
            ]

            handler._handle_archive_delete()

        # Child folders must be selected and deleted deepest-first.
        select_calls = mock_client.select_folder.call_args_list
        assert select_calls == [
            mock.call("Archive/Newsletters/2024/Q1"),
            mock.call("Archive/Newsletters/2024"),
            mock.call("Archive/Newsletters/2025"),
            mock.call("Archive/Newsletters"),
        ]

        # Child folders deleted before the target.
        assert mock_client.delete_folder.call_args_list == [
            mock.call("Archive/Newsletters/2024/Q1"),
            mock.call("Archive/Newsletters/2024"),
            mock.call("Archive/Newsletters/2025"),
            mock.call("Archive/Newsletters"),
        ]

        handler._serve_json.assert_called_once_with(
            {"status": "deleted", "source_folder": "Newsletters"}
        )

    def test_force_delete_with_non_default_delimiter(self, tmp_db_path: str) -> None:
        """Force-delete respects the server's hierarchy delimiter (e.g. '.')."""
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="Archive",
        )
        handler = _make_handler(tmp_db_path, mail_config)
        _make_archive_delete_request(handler, source_folder="Newsletters", force=True)

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [
                _make_folder("Archive", delimiter="."),
                _make_folder("Archive.Newsletters", delimiter="."),
                _make_folder("Archive.Newsletters.2024", delimiter="."),
                _make_folder("Archive.Newsletters.2024.Q1", delimiter="."),
            ]
            mock_client.search_uids.return_value = []

            handler._handle_archive_delete()

        select_calls = mock_client.select_folder.call_args_list
        assert select_calls == [
            mock.call("Archive.Newsletters.2024.Q1"),
            mock.call("Archive.Newsletters.2024"),
            mock.call("Archive.Newsletters"),
        ]

        assert mock_client.delete_folder.call_args_list == [
            mock.call("Archive.Newsletters.2024.Q1"),
            mock.call("Archive.Newsletters.2024"),
            mock.call("Archive.Newsletters"),
        ]

    def test_force_delete_empty_child_folder_still_deleted(
        self, tmp_db_path: str
    ) -> None:
        """A child folder with zero messages is still deleted (no expunge
        needed, but the DELETE must still be issued)."""
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="Archive",
        )
        handler = _make_handler(tmp_db_path, mail_config)
        _make_archive_delete_request(handler, source_folder="Parent", force=True)

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [
                _make_folder("Archive"),
                _make_folder("Archive/Parent"),
                _make_folder("Archive/Parent/Empty"),
            ]
            # Empty child returns no UIDs, target has UIDs.
            mock_client.search_uids.side_effect = [
                [],  # Empty child
                [5],  # Parent
            ]

            handler._handle_archive_delete()

        # Empty child: select_folder called, search_uids returns [],
        # delete_messages NOT called, but delete_folder IS called.
        assert mock_client.select_folder.call_args_list == [
            mock.call("Archive/Parent/Empty"),
            mock.call("Archive/Parent"),
        ]
        # Only one delete_messages call (for the parent, with UIDs).
        mock_client.delete_messages.assert_called_once_with([5])
        # Both folders deleted, child first.
        assert mock_client.delete_folder.call_args_list == [
            mock.call("Archive/Parent/Empty"),
            mock.call("Archive/Parent"),
        ]


class TestHandleArchiveDeleteNonForce:
    """Non-force rejection tests for ``_handle_archive_delete``."""

    def test_non_force_rejects_child_folders(self, tmp_db_path: str) -> None:
        """Non-force delete of a folder with child subfolders returns 409."""
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="Archive",
        )
        handler = _make_handler(tmp_db_path, mail_config)
        _make_archive_delete_request(handler, source_folder="Parent", force=False)

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [
                _make_folder("Archive"),
                _make_folder("Archive/Parent"),
                _make_folder("Archive/Parent/Child"),
            ]
            mock_client.select_folder.return_value = 0

            handler._handle_archive_delete()

        # Should return 409, not attempt deletion.
        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args[0][0]
        assert call_args["error"] == (
            "Folder 'Parent' has child folders. Use force: true to override."
        )
        mock_client.delete_folder.assert_not_called()

    def test_non_force_rejects_non_empty_folder(self, tmp_db_path: str) -> None:
        """Non-force delete of a folder with messages returns 409."""
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="Archive",
        )
        handler = _make_handler(tmp_db_path, mail_config)
        _make_archive_delete_request(handler, source_folder="Inbox", force=False)

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [
                _make_folder("Archive"),
                _make_folder("Archive/Inbox"),
            ]
            mock_client.select_folder.return_value = 5

            handler._handle_archive_delete()

        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args[0][0]
        assert "5 message(s)" in call_args["error"]
        mock_client.delete_folder.assert_not_called()
