"""Tests for ``ArchiveService.handle_archive_create`` (POST /archive-create)."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import ImapError
from tests.server._test_helpers import _archive_service, _FakeHandler


def _mail_config() -> MailConfig:
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="test",
        password="test",
        archive_root="Archive",
    )


def _make_handler(db_path: str, body: object) -> _FakeHandler:
    handler = _FakeHandler(db_path, mail_config=_mail_config())
    handler._serve_json = mock.MagicMock()
    handler._bad_request = mock.MagicMock()
    handler._send_response = mock.MagicMock()
    raw = json.dumps(body).encode("utf-8")
    handler.headers.get.return_value = len(raw)
    handler.rfile.read.return_value = raw
    return handler


def _make_folder(name: str, delimiter: str = "/") -> mock.Mock:
    f = mock.Mock()
    f.name = name
    f.delimiter = delimiter
    return f


class TestHandleArchiveCreate:
    def test_creates_new_leaf_folder_returns_201(self, tmp_db_path: str) -> None:
        handler = _make_handler(
            tmp_db_path, {"target_subfolder": "Compta", "confirm": True}
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            client = mock_cls.return_value.__enter__.return_value
            client.list_folders.return_value = [
                _make_folder("INBOX"),
                _make_folder("Archive"),
            ]
            _archive_service(handler).handle_archive_create(handler)

        assert client.create_folder.call_args_list == [mock.call("Archive/Compta")]
        handler._serve_json.assert_called_once_with(
            {"status": "created", "folder": "Compta"}, status=201
        )
        handler._bad_request.assert_not_called()

    def test_nested_path_creates_missing_parents(self, tmp_db_path: str) -> None:
        handler = _make_handler(
            tmp_db_path,
            {"target_subfolder": "Admin/Comptabilité", "confirm": True},
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            client = mock_cls.return_value.__enter__.return_value
            client.list_folders.return_value = [_make_folder("INBOX")]
            _archive_service(handler).handle_archive_create(handler)

        assert client.create_folder.call_args_list == [
            mock.call("Archive"),
            mock.call("Archive/Admin"),
            mock.call("Archive/Admin/Comptabilité"),
        ]
        handler._serve_json.assert_called_once_with(
            {"status": "created", "folder": "Admin/Comptabilité"}, status=201
        )

    def test_translates_to_server_delimiter(self, tmp_db_path: str) -> None:
        handler = _make_handler(
            tmp_db_path, {"target_subfolder": "Admin/Compta", "confirm": True}
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            client = mock_cls.return_value.__enter__.return_value
            client.list_folders.return_value = [
                _make_folder("INBOX", "."),
                _make_folder("Archive", "."),
                _make_folder("Archive.Admin", "."),
            ]
            _archive_service(handler).handle_archive_create(handler)

        assert client.create_folder.call_args_list == [
            mock.call("Archive.Admin.Compta")
        ]

    def test_existing_folder_returns_409(self, tmp_db_path: str) -> None:
        handler = _make_handler(
            tmp_db_path, {"target_subfolder": "Compta", "confirm": True}
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            client = mock_cls.return_value.__enter__.return_value
            client.list_folders.return_value = [
                _make_folder("Archive"),
                _make_folder("Archive/Compta"),
            ]
            _archive_service(handler).handle_archive_create(handler)

        client.create_folder.assert_not_called()
        args, kwargs = handler._serve_json.call_args
        assert kwargs["status"] == 409
        assert "already exists" in args[0]["error"]

    @pytest.mark.parametrize(
        "target",
        ["../outside", "Admin/../../outside", "..", "a//b", "./x"],
    )
    def test_path_escape_or_malformed_returns_400(
        self, tmp_db_path: str, target: str
    ) -> None:
        handler = _make_handler(
            tmp_db_path, {"target_subfolder": target, "confirm": True}
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            _archive_service(handler).handle_archive_create(handler)

        handler._bad_request.assert_called_once()
        mock_cls.assert_not_called()
        handler._serve_json.assert_not_called()

    @pytest.mark.parametrize(
        "body",
        [
            {"confirm": True},
            {"target_subfolder": "", "confirm": True},
            {"target_subfolder": "/", "confirm": True},
            {"target_subfolder": "Compta"},
            {"target_subfolder": "Compta", "confirm": False},
            {"target_subfolder": "Compta", "confirm": "yes"},
        ],
    )
    def test_missing_params_or_confirm_returns_400(
        self, tmp_db_path: str, body: dict[str, object]
    ) -> None:
        handler = _make_handler(tmp_db_path, body)
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            _archive_service(handler).handle_archive_create(handler)

        handler._bad_request.assert_called_once()
        mock_cls.assert_not_called()

    def test_imap_error_returns_502(self, tmp_db_path: str) -> None:
        handler = _make_handler(
            tmp_db_path, {"target_subfolder": "Compta", "confirm": True}
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            client = mock_cls.return_value.__enter__.return_value
            client.list_folders.return_value = [_make_folder("Archive")]
            client.create_folder.side_effect = ImapError("CREATE failed")
            _archive_service(handler).handle_archive_create(handler)

        _, kwargs = handler._send_response.call_args
        assert kwargs["status"] == 502
        handler._serve_json.assert_not_called()

    def test_imap_not_configured_returns_503(self, tmp_db_path: str) -> None:
        handler = _make_handler(
            tmp_db_path, {"target_subfolder": "Compta", "confirm": True}
        )
        handler.mail_config = None
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            _archive_service(handler).handle_archive_create(handler)

        mock_cls.assert_not_called()
        _, kwargs = handler._serve_json.call_args
        assert kwargs["status"] == 503
