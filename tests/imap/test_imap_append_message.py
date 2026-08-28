"""Unit tests for ``ImapClient.append_message`` and ``_parse_appenduid``."""

from __future__ import annotations

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import ImapClient, ImapError
from tests.conftest import _make_mock_imap_ssl


class TestParseAppenduid:
    """Tests for ``ImapClient._parse_appenduid``."""

    def test_extracts_uid_from_bytes(self) -> None:
        data = [b"[APPENDUID 12345 678] (Success)"]
        assert ImapClient._parse_appenduid(data) == 678

    def test_extracts_uid_from_string(self) -> None:
        data = ["[APPENDUID 12345 42]"]
        assert ImapClient._parse_appenduid(data) == 42

    def test_returns_none_when_no_appenduid(self) -> None:
        data = [b"APPEND completed"]
        assert ImapClient._parse_appenduid(data) is None

    def test_returns_none_for_empty_data(self) -> None:
        assert ImapClient._parse_appenduid([]) is None

    def test_returns_none_for_none_data(self) -> None:
        assert ImapClient._parse_appenduid(None) is None

    def test_ignores_non_str_items(self) -> None:
        data = [123, None, b"[APPENDUID 1 99]"]
        assert ImapClient._parse_appenduid(data) == 99

    def test_first_uid_wins(self) -> None:
        data = [b"[APPENDUID 1 10]", b"[APPENDUID 2 20]"]
        assert ImapClient._parse_appenduid(data) == 10


class TestAppendMessageReturn:
    """Integration-style tests for ``append_message`` return value."""

    def test_returns_uid_when_appenduid_present(self) -> None:
        imap_mock = _make_mock_imap_ssl()
        imap_mock.append.return_value = ("OK", [b"[APPENDUID 12345 42]"])
        config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="",
            username="user",
            password="pass",
        )
        client = ImapClient(config)
        client._imap = imap_mock
        result = client.append_message("Drafts", b"From: test\n\nBody")
        assert result == 42

    def test_returns_none_when_no_appenduid(self) -> None:
        imap_mock = _make_mock_imap_ssl()
        imap_mock.append.return_value = ("OK", [b"APPEND completed"])
        config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="",
            username="user",
            password="pass",
        )
        client = ImapClient(config)
        client._imap = imap_mock
        result = client.append_message("Drafts", b"From: test\n\nBody")
        assert result is None

    def test_raises_on_failure(self) -> None:
        imap_mock = _make_mock_imap_ssl()
        imap_mock.append.return_value = ("NO", [b"Mailbox full"])
        config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="",
            username="user",
            password="pass",
        )
        client = ImapClient(config)
        client._imap = imap_mock
        with pytest.raises(ImapError, match="APPEND"):
            client.append_message("Drafts", b"From: test\n\nBody")
