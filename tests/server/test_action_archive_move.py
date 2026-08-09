"""Unit tests for ``_BoardActionMixin._handle_archive_move``."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from tests.server._test_helpers import _FakeHandler


def _make_fake_handler(
    db_path: str,
    mail_config: object | None = None,
    body: bytes | None = None,
) -> _FakeHandler:
    """Create a ``_FakeHandler`` with a pre-configured request body."""
    handler = _FakeHandler(db_path, mail_config=mail_config)
    handler.headers = mock.MagicMock()
    if body is not None:
        handler.headers.get.return_value = str(len(body))
        handler.rfile = mock.MagicMock()
        handler.rfile.read.return_value = body
    else:
        handler.headers.get.return_value = "0"
        handler.rfile = mock.MagicMock()
        handler.rfile.read.return_value = b""
    # _serve_json is not on _FakeHandler by default; wire it.
    handler._serve_json = mock.MagicMock()
    return handler


def _json_body(data: dict[str, object]) -> bytes:
    return json.dumps(data).encode("utf-8")


class TestHandleArchiveMove:
    def test_move_by_message_id(self, tmp_path: str, cfg: object) -> None:
        """Successful move using message_id to locate the message."""
        from robotsix_auto_mail.imap.client import ImapClient

        fake_folder = mock.MagicMock()
        fake_folder.name = "Archive/Projects"
        fake_folder.delimiter = "/"
        fake_folder.attributes = ()

        target_folder = mock.MagicMock()
        target_folder.name = "Archive/Keep"
        target_folder.delimiter = "/"
        target_folder.attributes = ()

        mock_client = mock.MagicMock(spec=ImapClient)
        mock_client.list_folders.return_value = [fake_folder, target_folder]
        # Search finds the message in the source folder.
        mock_client.search_uids.side_effect = lambda criteria: (
            [42] if "Message-ID" in criteria else []
        )
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=None)

        body = _json_body(
            {
                "message_id": "<test@example.com>",
                "target_subfolder": "Keep",
            }
        )

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.ImapClient",
            return_value=mock_client,
        ):
            handler = _make_fake_handler(
                str(tmp_path / "test.db"),
                mail_config=cfg,
                body=body,
            )
            handler._handle_archive_move()

        handler._serve_json.assert_called_once()
        result = handler._serve_json.call_args[0][0]
        assert result["status"] == "moved"
        assert result["target_subfolder"] == "Keep"

    def test_move_by_uid_and_source_folder(
        self, tmp_path: str, cfg: object
    ) -> None:
        """Successful move using uid + source_folder."""
        from robotsix_auto_mail.imap.client import ImapClient

        fake_folder = mock.MagicMock()
        fake_folder.name = "Archive/Projects"
        fake_folder.delimiter = "/"
        fake_folder.attributes = ()

        mock_client = mock.MagicMock(spec=ImapClient)
        mock_client.list_folders.return_value = [fake_folder]
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=None)

        body = _json_body(
            {
                "uid": 42,
                "source_folder": "Projects",
                "target_subfolder": "Keep",
            }
        )

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.ImapClient",
            return_value=mock_client,
        ):
            handler = _make_fake_handler(
                str(tmp_path / "test.db"),
                mail_config=cfg,
                body=body,
            )
            handler._handle_archive_move()

        handler._serve_json.assert_called_once()
        result = handler._serve_json.call_args[0][0]
        assert result["status"] == "moved"

    def test_message_not_found(self, tmp_path: str, cfg: object) -> None:
        """Returns 404 when the message_id is not found in any archive folder."""
        from robotsix_auto_mail.imap.client import ImapClient

        fake_folder = mock.MagicMock()
        fake_folder.name = "Archive/Projects"
        fake_folder.delimiter = "/"
        fake_folder.attributes = ()

        mock_client = mock.MagicMock(spec=ImapClient)
        mock_client.list_folders.return_value = [fake_folder]
        # No results from any search.
        mock_client.search_uids.return_value = []
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=None)

        body = _json_body(
            {
                "message_id": "<missing@example.com>",
                "target_subfolder": "Keep",
            }
        )

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.ImapClient",
            return_value=mock_client,
        ):
            handler = _make_fake_handler(
                str(tmp_path / "test.db"),
                mail_config=cfg,
                body=body,
            )
            handler._handle_archive_move()

        handler._not_found.assert_called_once()

    def test_invalid_target_outside_archive_root(
        self, tmp_path: str, cfg: object
    ) -> None:
        """Returns 400 when target_subfolder contains '..' escape attempt."""
        from robotsix_auto_mail.imap.client import ImapClient

        mock_client = mock.MagicMock(spec=ImapClient)
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=None)

        body = _json_body(
            {
                "message_id": "<test@example.com>",
                "target_subfolder": "../INBOX",
            }
        )

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.ImapClient",
            return_value=mock_client,
        ):
            handler = _make_fake_handler(
                str(tmp_path / "test.db"),
                mail_config=cfg,
                body=body,
            )
            handler._handle_archive_move()

        handler._bad_request.assert_called_once()
        assert ".." in str(handler._bad_request.call_args[0][0])

    def test_missing_target_subfolder(
        self, tmp_path: str, cfg: object
    ) -> None:
        """Returns 400 when target_subfolder is missing."""
        body = _json_body(
            {
                "message_id": "<test@example.com>",
            }
        )

        handler = _make_fake_handler(
            str(tmp_path / "test.db"),
            mail_config=cfg,
            body=body,
        )
        handler._handle_archive_move()

        handler._bad_request.assert_called_once()
        assert "target_subfolder" in str(handler._bad_request.call_args[0][0])

    def test_missing_message_id_and_uid(
        self, tmp_path: str, cfg: object
    ) -> None:
        """Returns 400 when neither message_id nor uid is provided."""
        body = _json_body(
            {
                "target_subfolder": "Keep",
            }
        )

        handler = _make_fake_handler(
            str(tmp_path / "test.db"),
            mail_config=cfg,
            body=body,
        )
        handler._handle_archive_move()

        handler._bad_request.assert_called_once()
        assert "message_id" in str(handler._bad_request.call_args[0][0])

    def test_uid_without_source_folder(
        self, tmp_path: str, cfg: object
    ) -> None:
        """Returns 400 when uid is provided without source_folder."""
        body = _json_body(
            {
                "uid": 42,
                "target_subfolder": "Keep",
            }
        )

        handler = _make_fake_handler(
            str(tmp_path / "test.db"),
            mail_config=cfg,
            body=body,
        )
        handler._handle_archive_move()

        handler._bad_request.assert_called_once()
        assert "source_folder" in str(handler._bad_request.call_args[0][0])

    def test_no_mail_config_returns_503(
        self, tmp_path: str
    ) -> None:
        """Returns 503 when IMAP is not configured."""
        body = _json_body(
            {
                "message_id": "<test@example.com>",
                "target_subfolder": "Keep",
            }
        )

        handler = _make_fake_handler(
            str(tmp_path / "test.db"),
            mail_config=None,
            body=body,
        )
        handler._handle_archive_move()

        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args
        assert call_args[1]["status"] == 503

    def test_malformed_json(self, tmp_path: str, cfg: object) -> None:
        """Returns 400 on malformed JSON body."""
        handler = _make_fake_handler(
            str(tmp_path / "test.db"),
            mail_config=cfg,
            body=b"not json",
        )
        handler._handle_archive_move()

        handler._bad_request.assert_called_once()
        assert "Malformed" in str(handler._bad_request.call_args[0][0])

    def test_imap_error_returns_502(
        self, tmp_path: str, cfg: object
    ) -> None:
        """Returns 502 on IMAP errors."""
        from robotsix_auto_mail.imap.client import ImapClient
        from robotsix_auto_mail.imap.errors import ImapError

        mock_client = mock.MagicMock(spec=ImapClient)
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=None)
        mock_client.list_folders.side_effect = ImapError("connection lost")

        body = _json_body(
            {
                "message_id": "<test@example.com>",
                "target_subfolder": "Keep",
            }
        )

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.ImapClient",
            return_value=mock_client,
        ):
            handler = _make_fake_handler(
                str(tmp_path / "test.db"),
                mail_config=cfg,
                body=body,
            )
            handler._handle_archive_move()

        handler._send_response.assert_called_once()
        call_args = handler._send_response.call_args
        assert call_args[1]["status"] == 502

    def test_imap_message_not_found_error(
        self, tmp_path: str, cfg: object
    ) -> None:
        """Returns 404 on ImapMessageNotFoundError."""
        from robotsix_auto_mail.imap.client import ImapClient
        from robotsix_auto_mail.imap.errors import ImapMessageNotFoundError

        mock_client = mock.MagicMock(spec=ImapClient)
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=None)
        mock_client.list_folders.side_effect = ImapMessageNotFoundError(
            "UID 42 not found"
        )

        body = _json_body(
            {
                "message_id": "<test@example.com>",
                "target_subfolder": "Keep",
            }
        )

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.ImapClient",
            return_value=mock_client,
        ):
            handler = _make_fake_handler(
                str(tmp_path / "test.db"),
                mail_config=cfg,
                body=body,
            )
            handler._handle_archive_move()

        handler._not_found.assert_called_once()
