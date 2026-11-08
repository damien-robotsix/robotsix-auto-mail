"""Unit tests for the shared archive-path and JSON-body helpers."""

from __future__ import annotations

import io
import json
from unittest import mock

import pytest

from robotsix_auto_mail.imap import ImapError
from robotsix_auto_mail.server._archive_paths import (
    imap_delimiter,
    send_imap_failure,
    translate_archive_path,
)
from robotsix_auto_mail.server._request_helpers import read_json_object


def _folder(delimiter: str | None) -> mock.Mock:
    f = mock.Mock()
    f.delimiter = delimiter
    return f


class TestImapDelimiter:
    def test_first_non_empty_delimiter_wins(self) -> None:
        assert imap_delimiter([_folder(None), _folder("."), _folder("/")]) == "."

    def test_defaults_to_slash(self) -> None:
        assert imap_delimiter([]) == "/"
        assert imap_delimiter([_folder(None), _folder("")]) == "/"


class TestTranslateArchivePath:
    def test_simple_subfolder(self) -> None:
        assert translate_archive_path("Archive", "Compta", "/") == "Archive/Compta"

    def test_nested_subfolder(self) -> None:
        assert translate_archive_path("Archive", "Admin/Compta", "/") == (
            "Archive/Admin/Compta"
        )

    def test_empty_sub_is_root(self) -> None:
        assert translate_archive_path("Archive", "", "/") == "Archive"

    def test_root_rejected_when_not_allowed(self) -> None:
        assert translate_archive_path("Archive", "", "/", allow_root=False) is None

    def test_subfolder_allowed_when_root_disallowed(self) -> None:
        assert (
            translate_archive_path("Archive", "X", "/", allow_root=False) == "Archive/X"
        )

    @pytest.mark.parametrize(
        "sub",
        ["..", "../Other", "A/../../Other", "A/.."],
    )
    def test_dotdot_segment_rejected(self, sub: str) -> None:
        assert translate_archive_path("Archive", sub, "/") is None

    def test_dotdot_inside_name_is_not_a_segment(self) -> None:
        assert translate_archive_path("Archive", "a..b", "/") == "Archive/a..b"

    @pytest.mark.parametrize("sub", ["../Archive2", "../Archive2/X", "../Archive"])
    def test_sibling_prefix_rejected(self, sub: str) -> None:
        # "Archive2" shares the string prefix "Archive" but is a sibling of
        # the root, not a child of it.
        assert translate_archive_path("Archive", sub, "/") is None

    def test_child_named_like_sibling_is_accepted(self) -> None:
        # "2" lands under the root ("Archive/2"), never as "Archive2".
        assert translate_archive_path("Archive", "2", "/") == "Archive/2"

    def test_non_slash_delimiter(self) -> None:
        assert translate_archive_path("Archive", "Admin/Compta", ".") == (
            "Archive.Admin.Compta"
        )

    def test_non_slash_delimiter_translates_nested_root(self) -> None:
        assert translate_archive_path("Mail/Archive", "X", ".") == "Mail.Archive.X"
        assert translate_archive_path("Mail/Archive", "", ".") == "Mail.Archive"

    def test_non_slash_delimiter_rejects_dotdot(self) -> None:
        assert translate_archive_path("Archive", "../Archive2", ".") is None


class _Ctx:
    def __init__(self, headers: dict[str, object], body: bytes) -> None:
        self.headers = headers
        self.rfile = io.BytesIO(body)
        self._bad_request = mock.MagicMock()
        self._send_response = mock.MagicMock()


def _ctx_for(payload: bytes, length: object | None = None) -> _Ctx:
    return _Ctx({"Content-Length": len(payload) if length is None else length}, payload)


class TestReadJsonObject:
    def test_returns_object(self) -> None:
        ctx = _ctx_for(json.dumps({"a": 1}).encode())
        assert read_json_object(ctx) == {"a": 1}  # type: ignore[arg-type]
        ctx._bad_request.assert_not_called()

    def test_non_integer_content_length_is_malformed(self) -> None:
        ctx = _ctx_for(b"{}", length="abc")
        assert read_json_object(ctx) is None  # type: ignore[arg-type]
        ctx._bad_request.assert_called_once_with("Malformed JSON body")

    def test_empty_body_is_malformed(self) -> None:
        ctx = _ctx_for(b"")
        assert read_json_object(ctx) is None  # type: ignore[arg-type]
        ctx._bad_request.assert_called_once_with("Malformed JSON body")

    def test_invalid_json(self) -> None:
        ctx = _ctx_for(b"{not json")
        assert read_json_object(ctx) is None  # type: ignore[arg-type]
        ctx._bad_request.assert_called_once_with("Malformed JSON body")

    def test_invalid_utf8(self) -> None:
        ctx = _ctx_for(b"\xff\xfe\xfa")
        assert read_json_object(ctx) is None  # type: ignore[arg-type]
        ctx._bad_request.assert_called_once()

    def test_non_object(self) -> None:
        ctx = _ctx_for(b"[1, 2]")
        assert read_json_object(ctx) is None  # type: ignore[arg-type]
        ctx._bad_request.assert_called_once_with("JSON body must be an object")

    def test_too_large(self) -> None:
        ctx = _ctx_for(b"{}", length=11)
        assert read_json_object(ctx, max_bytes=10) is None  # type: ignore[arg-type]
        ctx._send_response.assert_called_once_with("Request body too large", status=413)

    def test_custom_responder(self) -> None:
        ctx = _ctx_for(b"{bad")
        calls: list[tuple[str, str]] = []
        result = read_json_object(
            ctx,  # type: ignore[arg-type]
            lambda kind, detail: calls.append((kind, detail)),
        )
        assert result is None
        assert calls
        assert calls[0][0] == "malformed"
        ctx._bad_request.assert_not_called()


class TestSendImapFailure:
    def test_imap_error(self) -> None:
        ctx = mock.MagicMock()
        send_imap_failure(ctx, ImapError("boom"), "folder delete")
        ctx._send_response.assert_called_once_with(
            "IMAP error during folder delete: boom", status=502
        )

    def test_os_error(self) -> None:
        ctx = mock.MagicMock()
        send_imap_failure(ctx, OSError("down"), "folder delete")
        ctx._send_response.assert_called_once_with(
            "IMAP connection error: down", status=502
        )
