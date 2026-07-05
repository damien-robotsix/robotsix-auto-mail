"""Unit tests for ``_imap_archive_move``.

Covers path-traversal safety (ValueError), folder-hierarchy creation
(including custom delimiter), and stale-UID fallback via Message-ID
search.
"""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from tests.server._test_helpers import _FakeHandler


class TestImapArchiveMove:
    def test_value_error_when_dest_escapes_root(self) -> None:
        """When ``_archive_dest_folder`` returns ``None``, ValueError
        is raised."""
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(":memory:", mail_config=mail_config)

        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
            mock.patch(
                "robotsix_auto_mail.server.adapters._archive_dest_folder",
                return_value=None,  # path traversal detected
            ),
        ):
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

            with pytest.raises(ValueError, match="escapes archive root"):
                handler._imap_archive_move(
                    mail_config,
                    imap_uid=1,
                    effective_root="my-archive",
                    subfolder="..",
                )

    def test_folder_hierarchy_created_level_by_level(self) -> None:
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(":memory:", mail_config=mail_config)

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]
            mock_client.search_uids.return_value = [7]

            handler._imap_archive_move(
                mail_config,
                imap_uid=7,
                effective_root="my-archive",
                subfolder="Lists/new-list",
                source_folder="INBOX",
                message_id="hier",
            )

        expected_calls = [
            mock.call("my-archive"),
            mock.call("my-archive/Lists"),
            mock.call("my-archive/Lists/new-list"),
        ]
        assert mock_client.create_folder.call_args_list == expected_calls
        mock_client.move_message.assert_called_once_with(7, "my-archive/Lists/new-list")

    def test_no_subfolder_creates_root_only(self) -> None:
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(":memory:", mail_config=mail_config)

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]
            mock_client.search_uids.return_value = [3]

            handler._imap_archive_move(
                mail_config,
                imap_uid=3,
                effective_root="my-archive",
                subfolder=None,
                source_folder="INBOX",
                message_id="rootonly",
            )

        mock_client.create_folder.assert_called_once_with("my-archive")
        mock_client.move_message.assert_called_once_with(3, "my-archive")

    def test_different_delimiter_respected(self) -> None:
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(":memory:", mail_config=mail_config)

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [mock.Mock(delimiter=".")]
            mock_client.search_uids.return_value = [23]

            handler._imap_archive_move(
                mail_config,
                imap_uid=23,
                effective_root="my-archive",
                subfolder="Lists/new-list",
                source_folder="INBOX",
                message_id="dot-delim",
            )

        expected_calls = [
            mock.call("my-archive"),
            mock.call("my-archive.Lists"),
            mock.call("my-archive.Lists.new-list"),
        ]
        assert mock_client.create_folder.call_args_list == expected_calls
        mock_client.move_message.assert_called_once_with(
            23, "my-archive.Lists.new-list"
        )

    def test_resolve_uid_fallback_uses_message_id(self) -> None:
        """When the stored UID is stale, the Message-ID fallback finds
        the correct UID."""
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(":memory:", mail_config=mail_config)

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

            call_count = [0]

            def _search_uids(criteria: str) -> list[int]:
                call_count[0] += 1
                if "UID 42" in criteria:
                    return []  # stale
                if "fallback-test" in criteria:
                    return [77]  # found via Message-ID
                return [42]

            mock_client.search_uids.side_effect = _search_uids

            handler._imap_archive_move(
                mail_config,
                imap_uid=42,
                effective_root="my-archive",
                subfolder=None,
                source_folder="INBOX",
                message_id="fallback-test",
            )

        # move_message called with the resolved UID (77), not the stale one.
        mock_client.move_message.assert_called_once_with(77, "my-archive")
