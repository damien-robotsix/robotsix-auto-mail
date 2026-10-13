"""Unit tests for ``ViewService.serve_archive_message``.

Covers the read-only ``GET /archive/message`` endpoint, which reads the
full body of an archived message by archive folder + UID (with a
Message-ID fallback), mirroring ``POST /archive-message-delete``.
"""

from __future__ import annotations

from unittest import mock

from tests.server._view_mixin_helpers import _FakeHandler

pytest_plugins = ["tests.server._view_mixin_helpers"]

_RAW_MESSAGE = (
    b"From: sender@example.com\r\n"
    b"To: me@example.com\r\n"
    b"Subject: Petit changement pour mon anniversaire\r\n"
    b"Message-ID: <anniv@example.com>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Joyeux anniversaire body content\r\n"
)


def _make_mock_client(
    *,
    folder_name: str = "robotsix-mail-archive/Anniversaire",
    search_results: dict[str, list[int]] | None = None,
    fetched: list[tuple[int, bytes]] | None = None,
):
    """Build a context-manager IMAP client stub for the archive folder."""
    from robotsix_auto_mail.imap.client import ImapClient

    fake_folder = mock.MagicMock()
    fake_folder.name = folder_name
    fake_folder.delimiter = "/"
    fake_folder.attributes = ()

    mock_client = mock.MagicMock(spec=ImapClient)
    mock_client.list_folders.return_value = [fake_folder]

    results = search_results or {}

    def _search(query: str) -> list[int]:
        return results.get(query, [])

    mock_client.search_uids.side_effect = _search
    mock_client.fetch_messages.return_value = (
        fetched if fetched is not None else [(5, _RAW_MESSAGE)]
    )
    mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
    mock_client.__exit__ = mock.MagicMock(return_value=None)
    return mock_client


class TestServeArchiveMessage:
    def test_aggregate_mode_returns_400(self, fake_db_path: str, cfg: object) -> None:
        handler = _FakeHandler(
            fake_db_path,
            mail_config=cfg,
            _aggregate=True,
            path="/archive/message?folder=Anniversaire&uid=5",
        )
        handler._serve_archive_message()
        handler._serve_json.assert_called_once()
        assert handler._serve_json.call_args[1]["status"] == 400

    def test_no_mail_config_returns_503(self, fake_db_path: str) -> None:
        handler = _FakeHandler(
            fake_db_path,
            mail_config=None,
            path="/archive/message?folder=Anniversaire&uid=5",
        )
        handler._serve_archive_message()
        handler._serve_json.assert_called_once_with(
            {"error": "IMAP not configured for this account"},
            status=503,
        )

    def test_missing_folder_returns_400(self, fake_db_path: str, cfg: object) -> None:
        handler = _FakeHandler(
            fake_db_path,
            mail_config=cfg,
            path="/archive/message?uid=5",
        )
        handler._serve_archive_message()
        handler._bad_request.assert_called_once()
        assert "folder" in str(handler._bad_request.call_args[0][0])

    def test_missing_uid_and_message_id_returns_400(
        self, fake_db_path: str, cfg: object
    ) -> None:
        handler = _FakeHandler(
            fake_db_path,
            mail_config=cfg,
            path="/archive/message?folder=Anniversaire",
        )
        handler._serve_archive_message()
        handler._bad_request.assert_called_once()

    def test_non_integer_uid_returns_400(self, fake_db_path: str, cfg: object) -> None:
        handler = _FakeHandler(
            fake_db_path,
            mail_config=cfg,
            path="/archive/message?folder=Anniversaire&uid=notanint",
        )
        handler._serve_archive_message()
        handler._bad_request.assert_called_once()

    def test_path_escape_returns_400(self, fake_db_path: str, cfg: object) -> None:
        handler = _FakeHandler(
            fake_db_path,
            mail_config=cfg,
            path="/archive/message?folder=../INBOX&uid=5",
        )
        handler._serve_archive_message()
        handler._bad_request.assert_called_once()
        assert "escapes" in str(handler._bad_request.call_args[0][0])

    def test_reads_body_by_uid(self, fake_db_path: str, cfg: object) -> None:
        mock_client = _make_mock_client(
            search_results={"UID 5": [5]},
            fetched=[(5, _RAW_MESSAGE)],
        )
        with mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            return_value=mock_client,
        ):
            handler = _FakeHandler(
                fake_db_path,
                mail_config=cfg,
                _current_account_id="gmailperso",
                path="/archive/message?folder=Anniversaire&uid=5",
            )
            handler._serve_archive_message()

        handler._serve_json.assert_called_once()
        payload = handler._serve_json.call_args[0][0]
        assert payload["account"] == "gmailperso"
        assert payload["folder"] == "Anniversaire"
        assert payload["uid"] == 5
        assert "anniversaire" in payload["body_plain"].lower()
        assert payload["attachments"] == []
        # Read-only: the message is fetched via BODY.PEEK, never marked seen.
        mock_client.fetch_messages.assert_called_once_with([5])

    def test_accepts_full_archive_path_folder(
        self, fake_db_path: str, cfg: object
    ) -> None:
        """A folder given as the full IMAP path (as returned by /search)
        resolves identically to the archive-root-relative form."""
        mock_client = _make_mock_client(
            search_results={"UID 5": [5]},
            fetched=[(5, _RAW_MESSAGE)],
        )
        with mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            return_value=mock_client,
        ):
            handler = _FakeHandler(
                fake_db_path,
                mail_config=cfg,
                path=(
                    "/archive/message?folder=robotsix-mail-archive/Anniversaire&uid=5"
                ),
            )
            handler._serve_archive_message()

        handler._serve_json.assert_called_once()
        mock_client.select_folder.assert_called_once_with(
            "robotsix-mail-archive/Anniversaire"
        )

    def test_stale_uid_falls_back_to_message_id(
        self, fake_db_path: str, cfg: object
    ) -> None:
        mock_client = _make_mock_client(
            search_results={
                "UID 99": [],  # stale uid
                'HEADER Message-ID "<anniv@example.com>"': [5],
            },
            fetched=[(5, _RAW_MESSAGE)],
        )
        with mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            return_value=mock_client,
        ):
            handler = _FakeHandler(
                fake_db_path,
                mail_config=cfg,
                path=(
                    "/archive/message?folder=Anniversaire&uid=99"
                    "&message_id=<anniv@example.com>"
                ),
            )
            handler._serve_archive_message()

        handler._serve_json.assert_called_once()
        payload = handler._serve_json.call_args[0][0]
        assert payload["uid"] == 5

    def test_resolves_by_message_id_only(self, fake_db_path: str, cfg: object) -> None:
        mock_client = _make_mock_client(
            search_results={'HEADER Message-ID "<anniv@example.com>"': [5]},
            fetched=[(5, _RAW_MESSAGE)],
        )
        with mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            return_value=mock_client,
        ):
            handler = _FakeHandler(
                fake_db_path,
                mail_config=cfg,
                path=(
                    "/archive/message?folder=Anniversaire"
                    "&message_id=<anniv@example.com>"
                ),
            )
            handler._serve_archive_message()

        handler._serve_json.assert_called_once()
        assert handler._serve_json.call_args[0][0]["uid"] == 5

    def test_unknown_folder_returns_404(self, fake_db_path: str, cfg: object) -> None:
        mock_client = _make_mock_client(
            folder_name="robotsix-mail-archive/Other",
        )
        with mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            return_value=mock_client,
        ):
            handler = _FakeHandler(
                fake_db_path,
                mail_config=cfg,
                path="/archive/message?folder=Nonexistent&uid=5",
            )
            handler._serve_archive_message()

        handler._not_found.assert_called_once()

    def test_unknown_uid_returns_404(self, fake_db_path: str, cfg: object) -> None:
        mock_client = _make_mock_client(search_results={"UID 5": []})
        with mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            return_value=mock_client,
        ):
            handler = _FakeHandler(
                fake_db_path,
                mail_config=cfg,
                path="/archive/message?folder=Anniversaire&uid=5",
            )
            handler._serve_archive_message()

        handler._not_found.assert_called_once()

    def test_imap_error_returns_502(self, fake_db_path: str, cfg: object) -> None:
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
                path="/archive/message?folder=Anniversaire&uid=5",
            )
            handler._serve_archive_message()

        handler._problem.assert_called_once()
        assert handler._problem.call_args[1]["status"] == 502
