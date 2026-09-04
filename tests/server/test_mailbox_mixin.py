"""Unit tests for ``_MailboxMixin`` — read-only /folders and /search chat API."""

from __future__ import annotations

from email.message import EmailMessage
from unittest import mock

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.server._mailbox_mixin import _MailboxMixin
from robotsix_auto_mail.server._view_mixin import _BoardViewMixin


class _FakeHandler(_BoardViewMixin, _MailboxMixin):
    """Concrete ``_MailboxMixin`` with protocol attributes wired to mocks."""

    def __init__(
        self,
        mail_config: MailConfig | None = None,
        *,
        path: str = "/",
        _aggregate: bool = False,
        _current_account_id: str | None = "ROBOTSIX",
    ) -> None:
        self.db_path = "test.db"
        self.mail_config = mail_config
        self.path = path
        self._aggregate = _aggregate
        self.accounts = None
        self._current_account_id = _current_account_id
        self._account_cookie = None
        self._send_response = mock.MagicMock()
        self._not_found = mock.MagicMock()
        self._bad_request = mock.MagicMock()
        self._serve_json = mock.MagicMock()


def _mock_client(**attrs: object) -> mock.MagicMock:
    from robotsix_auto_mail.imap.client import ImapClient

    client = mock.MagicMock(spec=ImapClient)
    client.__enter__ = mock.MagicMock(return_value=client)
    client.__exit__ = mock.MagicMock(return_value=None)
    for key, value in attrs.items():
        setattr(client, key, value)
    return client


def _folder(name: str, *, attributes: tuple[str, ...] = ()) -> mock.MagicMock:
    folder = mock.MagicMock()
    folder.name = name
    folder.delimiter = "/"
    folder.attributes = attributes
    return folder


def _raw_message(*, subject: str = "Invoice", has_attachment: bool = True) -> bytes:
    msg = EmailMessage()
    msg["Message-ID"] = "<abc@example.com>"
    msg["From"] = "Corentin Rabot <corentin@example.com>"
    msg["To"] = "ops@robotsix.net"
    msg["Subject"] = subject
    msg.set_content("Please find the STL files attached.")
    if has_attachment:
        msg.add_attachment(
            b"STLDATA",
            maintype="model",
            subtype="stl",
            filename="bracket.stl",
        )
    return msg.as_bytes()


def _envelope(
    uid: int, *, subject: str = "Invoice", date: str = "Mon, 01 Jan 2024 10:00:00 +0000"
) -> dict[str, object]:
    return {
        "uid": uid,
        "subject": subject,
        "from": "Corentin Rabot <corentin@example.com>",
        "to": "ops@robotsix.net",
        "date": date,
        "size": 100,
        "flags": ["\\Seen"],
        "message_id": "<abc@example.com>",
    }


# ---------------------------------------------------------------------------
# GET /folders
# ---------------------------------------------------------------------------


class TestServeFolders:
    def test_aggregate_returns_400(self) -> None:
        handler = _FakeHandler(_aggregate=True)
        handler._serve_folders()
        handler._serve_json.assert_called_once()
        assert handler._serve_json.call_args[1]["status"] == 400

    def test_no_mail_config_returns_503(self) -> None:
        handler = _FakeHandler(mail_config=None)
        handler._serve_folders()
        handler._serve_json.assert_called_once_with(
            {"error": "IMAP not configured for this account"},
            status=503,
        )

    def test_lists_complete_folder_tree_with_counts(self, cfg: MailConfig) -> None:
        client = _mock_client(
            list_folders=mock.MagicMock(
                return_value=[
                    _folder("INBOX"),
                    _folder("[Gmail]/All Mail"),
                    _folder("[Gmail]/Sent Mail"),
                ]
            ),
            status_folder=mock.MagicMock(return_value=(5, 2)),
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler = _FakeHandler(mail_config=cfg, path="/folders")
            handler._serve_folders()

        payload = handler._serve_json.call_args[0][0]
        assert payload["account"] == "ROBOTSIX"
        assert payload["delimiter"] == "/"
        names = [f["name"] for f in payload["folders"]]
        # INBOX and provider special-use folders must all be present.
        assert names == ["INBOX", "[Gmail]/All Mail", "[Gmail]/Sent Mail"]
        inbox = next(f for f in payload["folders"] if f["name"] == "INBOX")
        assert inbox["messages"] == 5
        assert inbox["unseen"] == 2
        assert inbox["flags"] == []
        # STATUS was attempted for every folder.
        assert client.status_folder.call_count == 3

    def test_status_failure_omits_counts(self, cfg: MailConfig) -> None:
        from robotsix_auto_mail.imap.errors import ImapError

        client = _mock_client(
            list_folders=mock.MagicMock(
                return_value=[
                    _folder("INBOX"),
                    _folder("[Gmail]", attributes=("\\Noselect", "\\HasChildren")),
                ]
            ),
            status_folder=mock.MagicMock(side_effect=ImapError("STATUS failed")),
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler = _FakeHandler(mail_config=cfg, path="/folders")
            handler._serve_folders()

        payload = handler._serve_json.call_args[0][0]
        assert len(payload["folders"]) == 2
        # Counts omitted (not present at all) when STATUS is unavailable.
        assert "messages" not in payload["folders"][0]
        assert "unseen" not in payload["folders"][0]
        assert payload["folders"][1]["flags"] == ["\\Noselect", "\\HasChildren"]

    def test_imap_error_returns_502(self, cfg: MailConfig) -> None:
        from robotsix_auto_mail.imap.errors import ImapError

        client = _mock_client(
            list_folders=mock.MagicMock(side_effect=ImapError("boom")),
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler = _FakeHandler(mail_config=cfg, path="/folders")
            handler._serve_folders()

        assert handler._send_response.call_args[1]["status"] == 502


# ---------------------------------------------------------------------------
# GET /search
# ---------------------------------------------------------------------------


class TestServeSearch:
    def test_aggregate_returns_400(self) -> None:
        handler = _FakeHandler(_aggregate=True)
        handler._serve_search()
        assert handler._serve_json.call_args[1]["status"] == 400

    def test_no_mail_config_returns_503(self) -> None:
        handler = _FakeHandler(mail_config=None)
        handler._serve_search()
        handler._serve_json.assert_called_once_with(
            {"error": "IMAP not configured for this account"},
            status=503,
        )

    def test_no_criteria_returns_400(self, cfg: MailConfig) -> None:
        handler = _FakeHandler(mail_config=cfg, path="/search")
        handler._serve_search()
        handler._bad_request.assert_called_once()
        assert "criteria" in str(handler._bad_request.call_args[0][0])

    def test_malformed_date_returns_400(self, cfg: MailConfig) -> None:
        handler = _FakeHandler(
            mail_config=cfg, path="/search?from=Rabot&since=not-a-date"
        )
        handler._serve_search()
        handler._bad_request.assert_called_once()
        assert "date" in str(handler._bad_request.call_args[0][0])

    def test_unknown_folder_returns_404(self, cfg: MailConfig) -> None:
        client = _mock_client(
            list_folders=mock.MagicMock(return_value=[_folder("INBOX")]),
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler = _FakeHandler(
                mail_config=cfg, path="/search?from=Rabot&folder=Missing"
            )
            handler._serve_search()
        handler._not_found.assert_called_once()

    def test_searches_single_folder_and_builds_criteria(self, cfg: MailConfig) -> None:
        client = _mock_client(
            list_folders=mock.MagicMock(return_value=[_folder("INBOX")]),
            search_uids=mock.MagicMock(return_value=[7]),
            fetch_envelopes=mock.MagicMock(return_value=[_envelope(7)]),
            fetch_messages=mock.MagicMock(return_value=[(7, _raw_message())]),
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler = _FakeHandler(
                mail_config=cfg,
                path="/search?account=ROBOTSIX&from=Rabot&subject=STL"
                "&text=attached&since=2024-01-01&before=2024-02-01"
                "&folder=INBOX",
            )
            handler._serve_search()

        # Criteria is AND-combined and dates converted to IMAP format.
        criteria = client.search_uids.call_args[0][0]
        assert 'FROM "Rabot"' in criteria
        assert 'SUBJECT "STL"' in criteria
        assert 'TEXT "attached"' in criteria
        assert "SINCE 01-Jan-2024" in criteria
        assert "BEFORE 01-Feb-2024" in criteria

        payload = handler._serve_json.call_args[0][0]
        assert payload["account"] == "ROBOTSIX"
        assert payload["count"] == 1
        msg = payload["messages"][0]
        assert msg["folder"] == "INBOX"
        assert msg["uid"] == 7
        assert msg["message_id"] == "<abc@example.com>"
        # Attachment summary derived from parsed MIME.
        assert msg["attachments"][0]["filename"] == "bracket.stl"
        assert msg["attachments"][0]["mime_type"] == "model/stl"

    def test_searches_all_selectable_folders_and_tags_each(
        self, cfg: MailConfig
    ) -> None:
        state: dict[str, object] = {"selected": []}

        def _select_folder(name: str) -> None:
            state["selected"].append(name)
            state["current"] = name

        def _search(*_args: object, **_kwargs: object) -> list[int]:
            if state["current"] == "INBOX":
                return [1, 2]
            return []

        client = _mock_client(
            list_folders=mock.MagicMock(
                return_value=[
                    _folder("INBOX"),
                    _folder("[Gmail]", attributes=("\\Noselect",)),
                    _folder("[Gmail]/All Mail"),
                    _folder("Projects"),
                ]
            ),
            select_folder=mock.MagicMock(side_effect=_select_folder),
            search_uids=mock.MagicMock(side_effect=_search),
            fetch_envelopes=mock.MagicMock(
                return_value=[
                    _envelope(1, subject="Old", date="Mon, 01 Jan 2024 10:00:00 +0000"),
                    _envelope(2, subject="New", date="Mon, 02 Jan 2024 10:00:00 +0000"),
                ]
            ),
            fetch_messages=mock.MagicMock(return_value=[(2, _raw_message())]),
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler = _FakeHandler(mail_config=cfg, path="/search?from=Rabot&limit=1")
            handler._serve_search()

        # Only the selectable folders (INBOX, All Mail, Projects) are searched —
        # the [Gmail] (\Noselect) container node is skipped.
        assert "INBOX" in state["selected"]
        assert "Projects" in state["selected"]
        assert "[Gmail]" not in state["selected"]

        # Newest-first + limit=1 → the "New" message (uid 2) is returned.
        payload = handler._serve_json.call_args[0][0]
        assert payload["count"] == 1
        assert payload["messages"][0]["uid"] == 2
        assert payload["messages"][0]["folder"] == "INBOX"

    def test_has_attachments_filter(self, cfg: MailConfig) -> None:
        client = _mock_client(
            list_folders=mock.MagicMock(return_value=[_folder("INBOX")]),
            search_uids=mock.MagicMock(return_value=[1, 2]),
            fetch_envelopes=mock.MagicMock(return_value=[_envelope(1), _envelope(2)]),
            # uid 1 has an attachment, uid 2 does not.
            fetch_messages=mock.MagicMock(
                return_value=[
                    (1, _raw_message(has_attachment=True)),
                    (2, _raw_message(has_attachment=False)),
                ]
            ),
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler = _FakeHandler(
                mail_config=cfg, path="/search?from=Rabot&has_attachments=true"
            )
            handler._serve_search()

        payload = handler._serve_json.call_args[0][0]
        uids = [m["uid"] for m in payload["messages"]]
        assert uids == [1]

    def test_imap_error_returns_502(self, cfg: MailConfig) -> None:
        from robotsix_auto_mail.imap.errors import ImapError

        client = _mock_client(
            list_folders=mock.MagicMock(side_effect=ImapError("boom")),
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler = _FakeHandler(mail_config=cfg, path="/search?from=Rabot")
            handler._serve_search()
        assert handler._send_response.call_args[1]["status"] == 502

    def test_non_ascii_value_sets_charset_utf8(self, cfg: MailConfig) -> None:
        client = _mock_client(
            list_folders=mock.MagicMock(return_value=[_folder("INBOX")]),
            search_uids=mock.MagicMock(return_value=[]),
            fetch_envelopes=mock.MagicMock(return_value=[]),
        )
        with mock.patch("robotsix_auto_mail.imap.ImapClient", return_value=client):
            handler = _FakeHandler(mail_config=cfg, path="/search?from=Caf%C3%A9")
            handler._serve_search()

        kwargs = client.search_uids.call_args[1]
        assert kwargs["charset"] == "UTF-8"
