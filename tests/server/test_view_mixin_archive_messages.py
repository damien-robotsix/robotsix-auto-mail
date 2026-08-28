"""Unit tests for ``_BoardViewMixin._serve_archive_messages``."""

from __future__ import annotations

from unittest import mock

import pytest

from tests.server._view_mixin_helpers import _FakeHandler

pytest_plugins = ["tests.server._view_mixin_helpers"]


class TestServeArchiveMessages:
    @pytest.fixture(autouse=True)
    def _patch_imports(self) -> None:
        pass

    def test_short_circuits_in_aggregate_mode(self, fake_db_path: str) -> None:
        """Aggregate mode returns an empty list without touching IMAP."""
        handler = _FakeHandler(
            fake_db_path,
            _aggregate=True,
            path="/archive/some-folder/messages",
        )
        handler._serve_archive_messages(folder="some-folder")
        handler._serve_json.assert_called_once_with(
            {"messages": [], "folder": "some-folder"}
        )

    def test_no_mail_config_returns_503(self, fake_db_path: str) -> None:
        """Returns 503 when no mail_config is set (no IMAP credentials)."""
        handler = _FakeHandler(
            fake_db_path,
            mail_config=None,
            path="/archive/some-folder/messages",
        )
        handler._serve_archive_messages(folder="some-folder")
        handler._serve_json.assert_called_once_with(
            {"error": "IMAP not configured for this account"},
            status=503,
        )

    def test_lists_messages_in_folder(self, fake_db_path: str, cfg: object) -> None:
        """Returns envelope metadata for messages in the folder."""
        from robotsix_auto_mail.imap.client import ImapClient

        # Build fake folder list.
        fake_folder = mock.MagicMock()
        fake_folder.name = "robotsix-mail-archive/Projects"
        fake_folder.delimiter = "/"
        fake_folder.attributes = ()

        mock_client = mock.MagicMock(spec=ImapClient)
        mock_client.list_folders.return_value = [fake_folder]
        mock_client.search_uids.return_value = [1, 2, 3]
        mock_client.fetch_envelopes.return_value = [
            {
                "uid": 1,
                "subject": "Test",
                "from": "A <a@x.com>",
                "date": "2024-01-01",
                "size": 100,
                "flags": ["\\Seen"],
                "message_id": "<test@example.com>",
            },
        ]
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=None)

        with mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            return_value=mock_client,
        ):
            handler = _FakeHandler(
                fake_db_path,
                mail_config=cfg,
                path="/archive/Projects/messages",
            )
            handler._serve_archive_messages(folder="Projects")

        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args[0][0]
        assert call_args["folder"] == "Projects"
        assert call_args["total"] == 3
        assert len(call_args["messages"]) == 1

    def test_empty_folder(self, fake_db_path: str, cfg: object) -> None:
        """An empty archive folder returns an empty message list."""
        from robotsix_auto_mail.imap.client import ImapClient

        fake_folder = mock.MagicMock()
        fake_folder.name = "robotsix-mail-archive/Empty"
        fake_folder.delimiter = "/"
        fake_folder.attributes = ()

        mock_client = mock.MagicMock(spec=ImapClient)
        mock_client.list_folders.return_value = [fake_folder]
        mock_client.search_uids.return_value = []
        mock_client.fetch_envelopes.return_value = []
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=None)

        with mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            return_value=mock_client,
        ):
            handler = _FakeHandler(
                fake_db_path,
                mail_config=cfg,
                path="/archive/Empty/messages",
            )
            handler._serve_archive_messages(folder="Empty")

        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args[0][0]
        assert call_args["total"] == 0
        assert call_args["messages"] == []

    def test_folder_not_found_returns_404(self, fake_db_path: str, cfg: object) -> None:
        """Returns 404 when the requested folder does not exist on IMAP."""
        from robotsix_auto_mail.imap.client import ImapClient

        fake_folder = mock.MagicMock()
        fake_folder.name = "robotsix-mail-archive/Other"
        fake_folder.delimiter = "/"
        fake_folder.attributes = ()

        mock_client = mock.MagicMock(spec=ImapClient)
        mock_client.list_folders.return_value = [fake_folder]
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=None)

        with mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            return_value=mock_client,
        ):
            handler = _FakeHandler(
                fake_db_path,
                mail_config=cfg,
                path="/archive/Nonexistent/messages",
            )
            handler._serve_archive_messages(folder="Nonexistent")

        handler._not_found.assert_called_once()

    def test_path_escape_returns_400(self, fake_db_path: str, cfg: object) -> None:
        """Returns 400 when the folder path contains '..' escape attempt."""
        from robotsix_auto_mail.imap.client import ImapClient

        fake_folder = mock.MagicMock()
        fake_folder.name = "robotsix-mail-archive"
        fake_folder.delimiter = "/"
        fake_folder.attributes = ()

        mock_client = mock.MagicMock(spec=ImapClient)
        mock_client.list_folders.return_value = [fake_folder]
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=None)

        with mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            return_value=mock_client,
        ):
            handler = _FakeHandler(
                fake_db_path,
                mail_config=cfg,
                path="/archive/../INBOX/messages",
            )
            handler._serve_archive_messages(folder="..")

        handler._bad_request.assert_called_once()
        assert "escapes" in str(handler._bad_request.call_args[0][0])

    def test_imap_error_returns_502(self, fake_db_path: str, cfg: object) -> None:
        """Returns 502 on IMAP errors."""
        from robotsix_auto_mail.imap.client import ImapClient
        from robotsix_auto_mail.imap.errors import ImapError

        mock_client = mock.MagicMock(spec=ImapClient)
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=None)
        mock_client.list_folders.side_effect = ImapError("connection refused")

        with mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            return_value=mock_client,
        ):
            handler = _FakeHandler(
                fake_db_path,
                mail_config=cfg,
                path="/archive/Projects/messages",
            )
            handler._serve_archive_messages(folder="Projects")

        handler._send_response.assert_called_once()
        call_args = handler._send_response.call_args
        assert call_args[1]["status"] == 502
        assert "IMAP error" in str(call_args[0][0])

    def test_respects_limit_query_param(self, fake_db_path: str, cfg: object) -> None:
        """The ?limit=N query param caps the number of messages returned."""
        from robotsix_auto_mail.imap.client import ImapClient

        fake_folder = mock.MagicMock()
        fake_folder.name = "robotsix-mail-archive/Projects"
        fake_folder.delimiter = "/"
        fake_folder.attributes = ()

        mock_client = mock.MagicMock(spec=ImapClient)
        mock_client.list_folders.return_value = [fake_folder]
        # 50 messages, but limit=10.
        mock_client.search_uids.return_value = list(range(1, 51))
        mock_client.fetch_envelopes.return_value = [
            {
                "uid": i,
                "subject": f"Msg {i}",
                "from": "",
                "date": "",
                "size": 0,
                "flags": [],
                "message_id": f"<msg{i}@example.com>",
            }
            for i in range(1, 11)
        ]
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=None)

        with mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            return_value=mock_client,
        ):
            handler = _FakeHandler(
                fake_db_path,
                mail_config=cfg,
                path="/archive/Projects/messages?limit=10",
            )
            handler._serve_archive_messages(folder="Projects")

        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args[0][0]
        assert call_args["total"] == 50
        assert call_args["shown"] == 10
        assert len(call_args["messages"]) == 10
