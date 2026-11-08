"""Regression tests pinning the ``handle_archive_*`` JSON handlers to the shared
archive-path and JSON-body helpers.

The helpers ``read_json_object`` (``_request_helpers``), ``imap_delimiter``,
``translate_archive_path`` and ``send_imap_failure`` (``_archive_paths``)
replaced per-handler inline copies.  These tests drive every JSON handler
through them and assert exact status codes and response bodies, so a change
to a helper's behaviour (or a handler that stops using it) is caught.

``handle_archive`` (form-encoded, via ``handle_post_action``) is covered in
``test_action_handle_archive.py`` / ``test_action_archive_and_delete.py``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, ClassVar
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import ImapError, ImapMessageNotFoundError
from robotsix_auto_mail.server._archive_service import ArchiveService
from robotsix_auto_mail.server._request_helpers import MAX_JSON_BODY_BYTES
from tests.server._test_helpers import _archive_service, _FakeHandler

Handler = Callable[[ArchiveService, _FakeHandler], None]


def _mail_config() -> MailConfig:
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="test",
        password="test",
        archive_root="Archive",
    )


def _set_raw_body(handler: _FakeHandler, raw: bytes, length: object = None) -> None:
    handler.headers.get.return_value = len(raw) if length is None else length
    handler.rfile.read.return_value = raw


def _make_handler(db_path: str, body: object) -> _FakeHandler:
    handler = _FakeHandler(db_path, mail_config=_mail_config())
    _set_raw_body(handler, json.dumps(body).encode("utf-8"))
    return handler


def _folder(name: str, delimiter: str | None = "/") -> mock.Mock:
    f = mock.Mock()
    f.name = name
    f.delimiter = delimiter
    f.attributes = []
    return f


# (id, handler call, a valid body, IMAP operation label in the 502 message)
_HANDLERS: list[tuple[str, Handler, dict[str, Any], str]] = [
    (
        "move",
        lambda s, h: s.handle_archive_move(h),
        {"uid": 5, "source_folder": "A", "target_subfolder": "B"},
        "archive move",
    ),
    (
        "delete",
        lambda s, h: s.handle_archive_delete(h),
        {"source_folder": "A", "confirm": True},
        "folder delete",
    ),
    (
        "message_delete",
        lambda s, h: s.handle_archive_message_delete(h),
        {"uid": 5, "source_folder": "A", "confirm": True},
        "message delete",
    ),
    (
        "rename",
        lambda s, h: s.handle_archive_rename(h),
        {"source_folder": "A", "target_path": "B", "confirm": True},
        "folder rename",
    ),
    (
        "create",
        lambda s, h: s.handle_archive_create(h),
        {"target_subfolder": "A", "confirm": True},
        "folder create",
    ),
]
_HANDLER_IDS = [h[0] for h in _HANDLERS]


# ---------------------------------------------------------------------------
# JSON-body parsing (read_json_object) — identical for every handler
# ---------------------------------------------------------------------------


class TestJsonBodyErrors:
    @pytest.mark.parametrize(
        ("raw", "length", "message"),
        [
            (b"", None, "Malformed JSON body"),
            (b"{}", "abc", "Malformed JSON body"),
            (b"{not json", None, "Malformed JSON body"),
            (b"\xff\xfe", None, "Malformed JSON body"),
            (b"[1, 2]", None, "JSON body must be an object"),
            (b'"str"', None, "JSON body must be an object"),
            (b"null", None, "JSON body must be an object"),
        ],
        ids=[
            "empty",
            "bad-length",
            "invalid",
            "bad-utf8",
            "array",
            "string",
            "null",
        ],
    )
    @pytest.mark.parametrize(
        ("_id", "call", "_body", "_op"), _HANDLERS, ids=_HANDLER_IDS
    )
    def test_bad_body_returns_400(
        self,
        tmp_db_path: str,
        _id: str,
        call: Handler,
        _body: dict[str, Any],
        _op: str,
        raw: bytes,
        length: object,
        message: str,
    ) -> None:
        handler = _FakeHandler(tmp_db_path, mail_config=_mail_config())
        _set_raw_body(handler, raw, length)
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            call(_archive_service(handler), handler)

        handler._bad_request.assert_called_once_with(message)
        handler._serve_json.assert_not_called()
        handler._send_response.assert_not_called()
        mock_cls.assert_not_called()

    @pytest.mark.parametrize(
        ("_id", "call", "body", "_op"), _HANDLERS, ids=_HANDLER_IDS
    )
    def test_oversized_body_returns_413(
        self,
        tmp_db_path: str,
        _id: str,
        call: Handler,
        body: dict[str, Any],
        _op: str,
    ) -> None:
        handler = _FakeHandler(tmp_db_path, mail_config=_mail_config())
        _set_raw_body(handler, json.dumps(body).encode(), MAX_JSON_BODY_BYTES + 1)
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            call(_archive_service(handler), handler)

        handler._send_response.assert_called_once_with(
            "Request body too large", status=413
        )
        handler.rfile.read.assert_not_called()
        handler._bad_request.assert_not_called()
        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# IMAP failures (send_imap_failure) — exact 502 bodies per handler
# ---------------------------------------------------------------------------


class TestImapFailures:
    @pytest.mark.parametrize(("_id", "call", "body", "op"), _HANDLERS, ids=_HANDLER_IDS)
    def test_imap_error_returns_502_with_operation(
        self,
        tmp_db_path: str,
        _id: str,
        call: Handler,
        body: dict[str, Any],
        op: str,
    ) -> None:
        handler = _make_handler(tmp_db_path, body)
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            client = mock_cls.return_value.__enter__.return_value
            client.list_folders.side_effect = ImapError("boom")
            call(_archive_service(handler), handler)

        handler._send_response.assert_called_once_with(
            f"IMAP error during {op}: boom", status=502
        )
        handler._serve_json.assert_not_called()

    @pytest.mark.parametrize(
        ("_id", "call", "body", "_op"), _HANDLERS, ids=_HANDLER_IDS
    )
    def test_os_error_returns_502_connection_error(
        self,
        tmp_db_path: str,
        _id: str,
        call: Handler,
        body: dict[str, Any],
        _op: str,
    ) -> None:
        handler = _make_handler(tmp_db_path, body)
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_cls.return_value.__enter__.side_effect = OSError("down")
            call(_archive_service(handler), handler)

        handler._send_response.assert_called_once_with(
            "IMAP connection error: down", status=502
        )
        handler._serve_json.assert_not_called()

    @pytest.mark.parametrize(
        ("_id", "call", "body", "_op"), _HANDLERS, ids=_HANDLER_IDS
    )
    def test_imap_not_configured_returns_503(
        self,
        tmp_db_path: str,
        _id: str,
        call: Handler,
        body: dict[str, Any],
        _op: str,
    ) -> None:
        handler = _make_handler(tmp_db_path, body)
        handler.mail_config = None
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            call(_archive_service(handler), handler)

        handler._serve_json.assert_called_once_with(
            {"error": "IMAP not configured for this account"}, status=503
        )
        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# translate_archive_path — defence in depth behind _validate_archive_path
# ---------------------------------------------------------------------------


class TestTranslateGuardInHandlers:
    """Bypass the request-context ``..`` guard so the handler's own
    ``translate_archive_path`` check is the one that must reject."""

    @pytest.mark.parametrize(
        ("call", "body", "message", "mutator"),
        [
            (
                lambda s, h: s.handle_archive_move(h),
                {"uid": 5, "source_folder": "../Other", "target_subfolder": "B"},
                "source_folder escapes archive root",
                "move_message",
            ),
            (
                lambda s, h: s.handle_archive_move(h),
                {"uid": 5, "source_folder": "A", "target_subfolder": "../Archive2"},
                "target_subfolder escapes archive root",
                "move_message",
            ),
            (
                lambda s, h: s.handle_archive_delete(h),
                {"source_folder": "../Archive2", "confirm": True, "force": True},
                "source_folder escapes archive root",
                "delete_folder",
            ),
            (
                lambda s, h: s.handle_archive_message_delete(h),
                {"uid": 5, "source_folder": "A/../..", "confirm": True},
                "source_folder escapes archive root",
                "delete_message",
            ),
            (
                lambda s, h: s.handle_archive_rename(h),
                {"source_folder": "..", "target_path": "B", "confirm": True},
                "source_folder escapes archive root",
                "rename_folder",
            ),
            (
                lambda s, h: s.handle_archive_rename(h),
                {"source_folder": "A", "target_path": "../B", "confirm": True},
                "target_name/target_path escapes archive root",
                "rename_folder",
            ),
            (
                lambda s, h: s.handle_archive_create(h),
                {"target_subfolder": "../Archive2", "confirm": True},
                "target_subfolder escapes archive root",
                "create_folder",
            ),
        ],
        ids=[
            "move-source",
            "move-target",
            "delete",
            "message-delete",
            "rename-source",
            "rename-target",
            "create",
        ],
    )
    def test_escape_rejected_by_translation(
        self,
        tmp_db_path: str,
        call: Handler,
        body: dict[str, Any],
        message: str,
        mutator: str,
    ) -> None:
        handler = _make_handler(tmp_db_path, body)
        handler._validate_archive_path = mock.MagicMock(  # type: ignore[method-assign]
            return_value=(True, "Archive")
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            client = mock_cls.return_value.__enter__.return_value
            client.list_folders.return_value = [
                _folder("Archive"),
                _folder("Archive/A"),
                _folder("Archive2"),
            ]
            call(_archive_service(handler), handler)

        handler._bad_request.assert_called_once_with(message)
        getattr(client, mutator).assert_not_called()
        handler._serve_json.assert_not_called()

    @pytest.mark.parametrize(
        ("_id", "call", "body", "_op"), _HANDLERS, ids=_HANDLER_IDS
    )
    def test_context_guard_rejects_dotdot_before_imap(
        self,
        tmp_db_path: str,
        _id: str,
        call: Handler,
        body: dict[str, Any],
        _op: str,
    ) -> None:
        key = "source_folder" if "source_folder" in body else "target_subfolder"
        handler = _make_handler(tmp_db_path, {**body, key: "../Other"})
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            call(_archive_service(handler), handler)

        handler._bad_request.assert_called_once_with("'../Other' escapes archive root")
        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# imap_delimiter — server delimiter drives translation in every handler
# ---------------------------------------------------------------------------


class TestServerDelimiter:
    _DOT_FOLDERS: ClassVar[list[mock.Mock]] = [
        _folder("INBOX", None),
        _folder("Archive", "."),
        _folder("Archive.A", "."),
        _folder("Archive.A.B", "."),
    ]

    def test_move_translates_source_and_target(self, tmp_db_path: str) -> None:
        handler = _make_handler(
            tmp_db_path,
            {"uid": 5, "source_folder": "A/B", "target_subfolder": "C/D"},
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            client = mock_cls.return_value.__enter__.return_value
            client.list_folders.return_value = self._DOT_FOLDERS
            _archive_service(handler).handle_archive_move(handler)

        client.select_folder.assert_called_with("Archive.A.B")
        client.move_message.assert_called_once_with(5, "Archive.C.D")
        handler._serve_json.assert_called_once_with(
            {
                "status": "moved",
                "message_id": "",
                "uid": 5,
                "source_folder": "Archive.A.B",
                "target_subfolder": "C/D",
            }
        )

    def test_delete_translates_source(self, tmp_db_path: str) -> None:
        handler = _make_handler(tmp_db_path, {"source_folder": "A/B", "confirm": True})
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            client = mock_cls.return_value.__enter__.return_value
            client.list_folders.return_value = self._DOT_FOLDERS
            client.select_folder.return_value = 0
            _archive_service(handler).handle_archive_delete(handler)

        client.delete_folder.assert_called_once_with("Archive.A.B")
        handler._serve_json.assert_called_once_with(
            {"status": "deleted", "source_folder": "A/B"}
        )

    def test_message_delete_translates_source(self, tmp_db_path: str) -> None:
        handler = _make_handler(
            tmp_db_path, {"uid": 5, "source_folder": "A/B", "confirm": True}
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            client = mock_cls.return_value.__enter__.return_value
            client.list_folders.return_value = self._DOT_FOLDERS
            client.search_uids.return_value = [5]
            _archive_service(handler).handle_archive_message_delete(handler)

        client.select_folder.assert_called_once_with("Archive.A.B")
        client.delete_message.assert_called_once_with(5)

    def test_rename_translates_source_and_target(self, tmp_db_path: str) -> None:
        handler = _make_handler(
            tmp_db_path,
            {"source_folder": "A/B", "target_name": "C", "confirm": True},
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            client = mock_cls.return_value.__enter__.return_value
            client.list_folders.return_value = self._DOT_FOLDERS
            _archive_service(handler).handle_archive_rename(handler)

        client.rename_folder.assert_called_once_with("Archive.A.B", "Archive.A.C")


# ---------------------------------------------------------------------------
# handle_archive_message_delete
# ---------------------------------------------------------------------------


class TestHandleArchiveMessageDelete:
    def _run(
        self, tmp_db_path: str, body: dict[str, Any], search: Any
    ) -> tuple[_FakeHandler, mock.MagicMock]:
        handler = _make_handler(tmp_db_path, body)
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            client = mock_cls.return_value.__enter__.return_value
            client.list_folders.return_value = [_folder("Archive/A")]
            client.search_uids.side_effect = search
            _archive_service(handler).handle_archive_message_delete(handler)
        return handler, client

    def test_success_by_uid(self, tmp_db_path: str) -> None:
        handler, client = self._run(
            tmp_db_path,
            {"uid": "5", "source_folder": "A", "confirm": True},
            [[5]],
        )
        client.search_uids.assert_called_once_with("UID 5")
        client.delete_message.assert_called_once_with(5)
        handler._serve_json.assert_called_once_with(
            {"status": "deleted", "uid": 5, "source_folder": "A"}
        )

    def test_stale_uid_falls_back_to_message_id(self, tmp_db_path: str) -> None:
        handler, client = self._run(
            tmp_db_path,
            {
                "uid": 5,
                "message_id": "<m@x>",
                "source_folder": "A",
                "confirm": True,
            },
            [[], [9]],
        )
        assert client.search_uids.call_args_list[1] == mock.call(
            'HEADER Message-ID "<m@x>"'
        )
        client.delete_message.assert_called_once_with(9)
        handler._serve_json.assert_called_once_with(
            {"status": "deleted", "uid": 9, "source_folder": "A"}
        )

    def test_message_id_only(self, tmp_db_path: str) -> None:
        _handler, client = self._run(
            tmp_db_path,
            {"message_id": "<m@x>", "source_folder": "A", "confirm": True},
            [[3]],
        )
        client.delete_message.assert_called_once_with(3)

    def test_not_found_returns_404(self, tmp_db_path: str) -> None:
        handler, client = self._run(
            tmp_db_path,
            {"uid": 5, "source_folder": "A", "confirm": True},
            [[]],
        )
        handler._not_found.assert_called_once()
        client.delete_message.assert_not_called()
        handler._serve_json.assert_not_called()

    def test_imap_message_not_found_returns_404(self, tmp_db_path: str) -> None:
        handler = _make_handler(
            tmp_db_path, {"uid": 5, "source_folder": "A", "confirm": True}
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            client = mock_cls.return_value.__enter__.return_value
            client.list_folders.return_value = [_folder("Archive/A")]
            client.search_uids.return_value = [5]
            client.delete_message.side_effect = ImapMessageNotFoundError("gone")
            _archive_service(handler).handle_archive_message_delete(handler)

        handler._not_found.assert_called_once()
        handler._send_response.assert_not_called()

    @pytest.mark.parametrize(
        ("body", "message"),
        [
            (
                {"uid": "abc", "source_folder": "A", "confirm": True},
                "uid must be an integer",
            ),
            (
                {"uid": [1], "source_folder": "A", "confirm": True},
                "uid must be an integer",
            ),
            (
                {"source_folder": "A", "confirm": True},
                "At least one of uid or message_id is required",
            ),
            ({"uid": 5, "confirm": True}, "source_folder is required"),
            (
                {"uid": 5, "source_folder": "A"},
                "confirm must be true — message deletion is irreversible",
            ),
        ],
    )
    def test_validation_errors(
        self, tmp_db_path: str, body: dict[str, Any], message: str
    ) -> None:
        handler = _make_handler(tmp_db_path, body)
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            _archive_service(handler).handle_archive_message_delete(handler)

        handler._bad_request.assert_called_once_with(message)
        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# handle_archive_rename
# ---------------------------------------------------------------------------


class TestHandleArchiveRename:
    def _run(
        self, tmp_db_path: str, body: dict[str, Any], folders: list[str]
    ) -> tuple[_FakeHandler, mock.MagicMock]:
        handler = _make_handler(tmp_db_path, body)
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            client = mock_cls.return_value.__enter__.return_value
            client.list_folders.return_value = [_folder(n) for n in folders]
            _archive_service(handler).handle_archive_rename(handler)
        return handler, client

    def test_target_name_keeps_parent(self, tmp_db_path: str) -> None:
        handler, client = self._run(
            tmp_db_path,
            {"source_folder": "Admin/Old", "target_name": "New", "confirm": True},
            ["Archive", "Archive/Admin", "Archive/Admin/Old"],
        )
        client.rename_folder.assert_called_once_with(
            "Archive/Admin/Old", "Archive/Admin/New"
        )
        handler._serve_json.assert_called_once_with(
            {"status": "renamed", "source_folder": "Admin/Old", "target": "Admin/New"}
        )

    def test_target_path_takes_precedence(self, tmp_db_path: str) -> None:
        handler, client = self._run(
            tmp_db_path,
            {
                "source_folder": "Admin/Old",
                "target_name": "Ignored",
                "target_path": "Other/New",
                "confirm": True,
            },
            ["Archive/Admin/Old"],
        )
        client.rename_folder.assert_called_once_with(
            "Archive/Admin/Old", "Archive/Other/New"
        )
        handler._serve_json.assert_called_once_with(
            {"status": "renamed", "source_folder": "Admin/Old", "target": "Other/New"}
        )

    def test_missing_source_returns_404(self, tmp_db_path: str) -> None:
        handler, client = self._run(
            tmp_db_path,
            {"source_folder": "Old", "target_name": "New", "confirm": True},
            ["Archive"],
        )
        handler._not_found.assert_called_once()
        client.rename_folder.assert_not_called()

    def test_existing_target_returns_409(self, tmp_db_path: str) -> None:
        handler, client = self._run(
            tmp_db_path,
            {"source_folder": "Old", "target_name": "New", "confirm": True},
            ["Archive/Old", "Archive/New"],
        )
        handler._serve_json.assert_called_once_with(
            {"error": "Target folder 'New' already exists"}, status=409
        )
        client.rename_folder.assert_not_called()

    @pytest.mark.parametrize(
        ("body", "message"),
        [
            ({"target_name": "New", "confirm": True}, "source_folder is required"),
            (
                {"source_folder": "Old", "target_name": "New"},
                "confirm must be true — folder rename is irreversible",
            ),
            (
                {"source_folder": "Old", "confirm": True},
                "target_name or target_path is required",
            ),
            (
                {"source_folder": "Old", "target_name": "..", "confirm": True},
                "'..' escapes archive root",
            ),
        ],
    )
    def test_validation_errors(
        self, tmp_db_path: str, body: dict[str, Any], message: str
    ) -> None:
        handler = _make_handler(tmp_db_path, body)
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            _archive_service(handler).handle_archive_rename(handler)

        handler._bad_request.assert_called_once_with(message)
        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# handle_archive_delete / handle_archive_move — remaining status paths
# ---------------------------------------------------------------------------


class TestHandleArchiveDeleteStatus:
    def test_missing_folder_returns_404(self, tmp_db_path: str) -> None:
        handler = _make_handler(tmp_db_path, {"source_folder": "A", "confirm": True})
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            client = mock_cls.return_value.__enter__.return_value
            client.list_folders.return_value = [_folder("Archive")]
            _archive_service(handler).handle_archive_delete(handler)

        handler._not_found.assert_called_once()
        client.delete_folder.assert_not_called()

    @pytest.mark.parametrize(
        ("body", "message"),
        [
            ({"confirm": True}, "source_folder is required"),
            (
                {"source_folder": "A"},
                "confirm must be true — folder deletion is irreversible",
            ),
        ],
    )
    def test_validation_errors(
        self, tmp_db_path: str, body: dict[str, Any], message: str
    ) -> None:
        handler = _make_handler(tmp_db_path, body)
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            _archive_service(handler).handle_archive_delete(handler)

        handler._bad_request.assert_called_once_with(message)
        mock_cls.assert_not_called()


class TestHandleArchiveMoveSearch:
    def test_message_id_search_skips_sibling_root(self, tmp_db_path: str) -> None:
        """``Archive2`` shares the root's string prefix but is not searched."""
        handler = _make_handler(
            tmp_db_path, {"message_id": "<m@x>", "target_subfolder": "Keep"}
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            client = mock_cls.return_value.__enter__.return_value
            client.list_folders.return_value = [
                _folder("Archive2"),
                _folder("Archive/X"),
            ]
            client.search_uids.return_value = [7]
            _archive_service(handler).handle_archive_move(handler)

        assert mock.call("Archive2") not in client.select_folder.call_args_list
        client.move_message.assert_called_once_with(7, "Archive/Keep")
        handler._serve_json.assert_called_once_with(
            {
                "status": "moved",
                "message_id": "<m@x>",
                "uid": 7,
                "source_folder": "Archive/X",
                "target_subfolder": "Keep",
            }
        )

    def test_uid_non_integer_returns_400(self, tmp_db_path: str) -> None:
        handler = _make_handler(
            tmp_db_path,
            {"uid": "x", "source_folder": "A", "target_subfolder": "B"},
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            _archive_service(handler).handle_archive_move(handler)

        handler._bad_request.assert_called_once_with("uid must be an integer")
        mock_cls.assert_not_called()
