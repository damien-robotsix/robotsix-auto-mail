"""Unit tests for archive-scoped addressing, unzip-during-push, and
provenance metadata in ``_AttachmentMixin._handle_push_to_file_hub``.

Complements ``test_attachment_mixin.py`` (board-message path) with the
three capabilities added for archive-resident mail: folder+uid
addressing, zip expansion, and metadata forwarding to file-hub.
"""

from __future__ import annotations

import io
import json
import zipfile
from email.message import EmailMessage
from typing import Any
from unittest import mock

from robotsix_auto_mail.config import MailAccountsConfig, MailConfig
from robotsix_auto_mail.server import _attachment_mixin
from robotsix_auto_mail.server._attachment_mixin import _AttachmentMixin


def _make_accounts(file_hub_url: str = "http://file-hub:8080") -> MailAccountsConfig:
    return MailAccountsConfig(accounts=[], file_hub_url=file_hub_url)


def _make_mail_config(archive_root: str = "robotsix-mail-archive") -> MailConfig:
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="test",
        password="test",
        archive_root=archive_root,
    )


def _make_zip(files: dict[str, bytes]) -> bytes:
    """Build in-memory zip bytes containing *files* (name -> content)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _make_mime(
    subject: str,
    sender: str,
    message_id: str,
    date: str,
    attachments: list[tuple[str, str, bytes]],
) -> bytes:
    """Build a MIME message with binary-safe (base64) attachments."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["Message-ID"] = message_id
    msg["Date"] = date
    msg.set_content("Body text")
    for filename, content_type, payload in attachments:
        maintype, subtype = content_type.split("/", 1)
        msg.add_attachment(
            payload, maintype=maintype, subtype=subtype, filename=filename
        )
    return msg.as_bytes()


class _ArchiveFakeHandler(_AttachmentMixin):
    """Concrete handler wiring ``BoardHandlerProtocol`` attributes to mocks."""

    def __init__(
        self,
        mail_config: MailConfig | None,
        accounts: MailAccountsConfig | None,
        account_id: str | None = "ROBOTSIX",
    ) -> None:
        self.db_path = ":memory:"
        self.mail_config = mail_config
        self.accounts = accounts
        self._current_account_id = account_id
        self._aggregate = False
        self.headers = mock.MagicMock()
        self.rfile = mock.MagicMock()
        self._send_response = mock.MagicMock()
        self._not_found = mock.MagicMock()
        self._bad_request = mock.MagicMock()
        self._serve_json = mock.MagicMock()

    def _problem(
        self,
        status: int,
        kind: str,
        title: str,
        detail: str,
        instance: str | None = None,
    ) -> None:
        self._serve_json(
            {
                "type": f"urn:robotsix:error:{kind}",
                "title": title,
                "detail": detail,
            },
            status=status,
        )

    def _validate_archive_path(self, *folders: str) -> tuple[bool, str]:
        for folder in folders:
            if ".." in folder.split("/"):
                self._bad_request(f"'{folder}' escapes archive root")
                return False, ""
        assert self.mail_config is not None
        return True, self.mail_config.archive_root


def _set_body(handler: _ArchiveFakeHandler, body: dict[str, Any]) -> None:
    raw = json.dumps(body).encode()
    handler.headers.get.return_value = len(raw)
    handler.rfile.read.return_value = raw


def _mock_imap(raw_email: bytes, uid: int = 4213) -> mock.MagicMock:
    """Build an ImapClient mock resolving *uid* and returning *raw_email*."""
    client = mock.MagicMock()
    client.__enter__ = mock.MagicMock(return_value=client)
    client.__exit__ = mock.MagicMock(return_value=False)
    client.list_folders.return_value = [mock.MagicMock(delimiter="/")]
    client.search_uids.return_value = [uid]
    client.fetch_messages.return_value = [(uid, raw_email)]
    return client


def _mock_httpx() -> tuple[mock.MagicMock, mock.MagicMock]:
    """Build an httpx module mock whose POST returns a 200 upload result."""
    httpx_mod = mock.MagicMock()
    client = mock.MagicMock()
    client.__enter__ = mock.MagicMock(return_value=client)
    client.__exit__ = mock.MagicMock(return_value=False)

    def _post(url: str, files: Any, data: Any) -> mock.MagicMock:
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "id": "id-" + files["file"][0],
            "filename": files["file"][0],
            "size": len(files["file"][1]),
            "content_type": files["file"][2],
        }
        return resp

    client.post.side_effect = _post
    httpx_mod.Client.return_value = client
    return httpx_mod, client


class TestArchiveUnzipHappyPath:
    def test_two_zip_attachments_expand_to_stls_with_metadata(self) -> None:
        raw_email = _make_mime(
            subject="Re: question sur jumeau numerique",
            sender="Corentin Rabot <corentin@example.com>",
            message_id="<rabot-31aug@example.com>",
            date="Sun, 31 Aug 2026 10:00:00 +0200",
            attachments=[
                (
                    "Old_structure.stl.zip",
                    "application/zip",
                    _make_zip({"Old_structure.stl": b"solid old\n"}),
                ),
                (
                    "20260821_structure_simple-correction-test.zip",
                    "application/zip",
                    _make_zip({"20260821_structure.stl": b"solid new\n"}),
                ),
            ],
        )
        handler = _ArchiveFakeHandler(_make_mail_config(), _make_accounts())
        _set_body(handler, {"source_folder": "BHealthcare", "uid": 4213})

        imap = _mock_imap(raw_email)
        httpx_mod, httpx_client = _mock_httpx()
        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=imap),
            mock.patch.object(_attachment_mixin, "httpx", httpx_mod),
        ):
            handler._handle_push_to_file_hub("<rabot-31aug@example.com>")

        handler._send_response.assert_called_once()
        body = json.loads(handler._send_response.call_args[0][0])
        # Two STL files land (not the two zips).
        landed = sorted(a["filename"] for a in body["attachments"])
        assert landed == ["20260821_structure.stl", "Old_structure.stl"]

        # Provenance metadata forwarded per uploaded file.
        metas = [
            json.loads(call.kwargs["data"]["metadata"])
            for call in httpx_client.post.call_args_list
        ]
        by_file = {m["zip_name"]: m for m in metas}
        old = by_file["Old_structure.stl.zip"]
        assert old["source_folder"] == "BHealthcare"
        assert old["source_account"] == "ROBOTSIX"
        assert old["mail_subject"] == "Re: question sur jumeau numerique"
        assert "Corentin Rabot" in old["mail_sender"]
        assert old["source_message_id"] == "<rabot-31aug@example.com>"
        assert old["attachment_filename"] == "Old_structure.stl.zip"

    def test_unzip_false_pushes_raw_zip(self) -> None:
        zip_bytes = _make_zip({"inner.stl": b"solid\n"})
        raw_email = _make_mime(
            subject="S",
            sender="a@b.com",
            message_id="<m@x>",
            date="Sun, 31 Aug 2026 10:00:00 +0200",
            attachments=[("model.stl.zip", "application/zip", zip_bytes)],
        )
        handler = _ArchiveFakeHandler(_make_mail_config(), _make_accounts())
        _set_body(
            handler,
            {"source_folder": "BHealthcare", "uid": 4213, "unzip": False},
        )

        imap = _mock_imap(raw_email)
        httpx_mod, httpx_client = _mock_httpx()
        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=imap),
            mock.patch.object(_attachment_mixin, "httpx", httpx_mod),
        ):
            handler._handle_push_to_file_hub("<m@x>")

        body = json.loads(handler._send_response.call_args[0][0])
        assert [a["filename"] for a in body["attachments"]] == ["model.stl.zip"]
        # The uploaded bytes are the raw zip container.
        uploaded = httpx_client.post.call_args_list[0].kwargs["files"]["file"][1]
        assert uploaded == zip_bytes

    def test_caller_context_and_tags_forwarded(self) -> None:
        raw_email = _make_mime(
            subject="S",
            sender="a@b.com",
            message_id="<m@x>",
            date="Sun, 31 Aug 2026 10:00:00 +0200",
            attachments=[("plain.txt", "text/plain", b"hello")],
        )
        handler = _ArchiveFakeHandler(_make_mail_config(), _make_accounts())
        _set_body(
            handler,
            {
                "source_folder": "BHealthcare",
                "uid": 4213,
                "context": "digital-twin STLs",
                "tags": ["stl", "twin"],
            },
        )

        imap = _mock_imap(raw_email)
        httpx_mod, httpx_client = _mock_httpx()
        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=imap),
            mock.patch.object(_attachment_mixin, "httpx", httpx_mod),
        ):
            handler._handle_push_to_file_hub("<m@x>")

        meta = json.loads(
            httpx_client.post.call_args_list[0].kwargs["data"]["metadata"]
        )
        assert meta["context"] == "digital-twin STLs"
        assert meta["tags"] == ["stl", "twin"]


class TestArchiveErrors:
    def test_missing_source_folder(self) -> None:
        handler = _ArchiveFakeHandler(_make_mail_config(), _make_accounts())
        _set_body(handler, {"uid": 4213})
        handler._handle_push_to_file_hub("<m@x>")
        handler._bad_request.assert_called_once()

    def test_uid_not_found_returns_404(self) -> None:
        raw_email = _make_mime(
            subject="S",
            sender="a@b.com",
            message_id="<m@x>",
            date="Sun, 31 Aug 2026 10:00:00 +0200",
            attachments=[("a.txt", "text/plain", b"x")],
        )
        handler = _ArchiveFakeHandler(_make_mail_config(), _make_accounts())
        _set_body(handler, {"source_folder": "BHealthcare", "uid": 9999})

        imap = _mock_imap(raw_email)
        imap.search_uids.return_value = []  # uid + message-id both miss
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=imap):
            handler._handle_push_to_file_hub("<m@x>")
        handler._not_found.assert_called_once()

    def test_path_escape_returns_400(self) -> None:
        handler = _ArchiveFakeHandler(_make_mail_config(), _make_accounts())
        _set_body(handler, {"source_folder": "../etc", "uid": 4213})
        handler._handle_push_to_file_hub("<m@x>")
        handler._bad_request.assert_called_once()

    def test_zip_bomb_cap_exceeded_returns_400(self) -> None:
        raw_email = _make_mime(
            subject="S",
            sender="a@b.com",
            message_id="<m@x>",
            date="Sun, 31 Aug 2026 10:00:00 +0200",
            attachments=[
                ("bomb.zip", "application/zip", _make_zip({"big.bin": b"x" * 4096})),
            ],
        )
        handler = _ArchiveFakeHandler(_make_mail_config(), _make_accounts())
        _set_body(handler, {"source_folder": "BHealthcare", "uid": 4213})

        imap = _mock_imap(raw_email)
        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=imap),
            mock.patch.object(_attachment_mixin, "_MAX_UNZIP_TOTAL_BYTES", 1),
        ):
            handler._handle_push_to_file_hub("<m@x>")

        handler._serve_json.assert_called_once()
        args = handler._serve_json.call_args
        assert args[1]["status"] == 400
        assert args[0][0]["type"] == "urn:robotsix:error:zip-too-large"
        handler._send_response.assert_not_called()
