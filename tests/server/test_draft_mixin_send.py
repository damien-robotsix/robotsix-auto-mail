"""Unit tests for ``_handle_send_draft``.

Drives the mixin directly against a mock handler *self*, isolating the
logic from the HTTP transport and covering branches that integration
tests miss (self-reply guard, empty-draft guard, reply_mode validation,
etc.).
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from tests.server._test_helpers import _DraftMixinFakeHandler
from tests.server.conftest_helpers import _seed_draft_record


class TestHandleSendDraft:
    """Tests for ``_handle_send_draft``."""

    def _setup_handler(
        self, db_path: str, message_id: str, reply_mode: str = "reply"
    ) -> _DraftMixinFakeHandler:
        handler = _DraftMixinFakeHandler(
            db_path,
            mail_config=MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="me@example.com",
                password="s3cret",
            ),
        )
        handler.headers.get.return_value = 200
        handler.rfile.read.return_value = (
            f"message_id={message_id}&reply_mode={reply_mode}&redirect_to=/board"
        ).encode("utf-8")
        return handler

    def test_invalid_reply_mode_returns_400(self, single_db: str) -> None:
        _seed_draft_record(
            single_db,
            "bad-mode",
            sender="sender@x.com",
            subject="Test",
            draft_text="Some draft",
        )
        handler = self._setup_handler(single_db, "bad-mode", reply_mode="invalid")

        with (
            mock.patch("robotsix_auto_mail.db.update_sent_reply_text"),
            mock.patch("robotsix_auto_mail.triage.delete_triage_decision"),
            mock.patch("robotsix_auto_mail.smtp.SmtpClient"),
        ):
            handler._handle_send_draft()

        handler._bad_request.assert_called_once()
        assert "Invalid reply_mode" in str(handler._bad_request.call_args[0][0])

    def test_self_reply_guard_returns_400(self, single_db: str) -> None:
        _seed_draft_record(
            single_db,
            "self-reply",
            sender="me@example.com",
            subject="Test",
            draft_text="Some draft",
        )
        handler = self._setup_handler(single_db, "self-reply")

        with (
            mock.patch("robotsix_auto_mail.db.update_sent_reply_text"),
            mock.patch("robotsix_auto_mail.triage.delete_triage_decision"),
            mock.patch("robotsix_auto_mail.smtp.SmtpClient"),
        ):
            handler._handle_send_draft()

        handler._bad_request.assert_called_once()
        assert "Refusing to send a reply to your own address" in str(
            handler._bad_request.call_args[0][0]
        )

    def test_empty_draft_guard_returns_400(self, single_db: str) -> None:
        _seed_draft_record(
            single_db,
            "empty-draft",
            sender="sender@x.com",
            subject="Test",
            draft_text="   ",
        )
        handler = self._setup_handler(single_db, "empty-draft")

        with (
            mock.patch("robotsix_auto_mail.db.update_sent_reply_text"),
            mock.patch("robotsix_auto_mail.triage.delete_triage_decision"),
            mock.patch("robotsix_auto_mail.smtp.SmtpClient"),
        ):
            handler._handle_send_draft()

        handler._bad_request.assert_called_once()
        assert "Draft is empty" in str(handler._bad_request.call_args[0][0])

    def test_smtp_not_configured_returns_400(self, single_db: str) -> None:
        _seed_draft_record(
            single_db,
            "no-smtp",
            sender="sender@x.com",
            subject="Test",
            draft_text="Some draft",
        )
        handler = _DraftMixinFakeHandler(single_db, mail_config=None)
        handler.headers.get.return_value = 200
        handler.rfile.read.return_value = (
            b"message_id=no-smtp&reply_mode=reply&redirect_to=/board"
        )

        with (
            mock.patch("robotsix_auto_mail.db.update_sent_reply_text"),
            mock.patch("robotsix_auto_mail.triage.delete_triage_decision"),
        ):
            handler._handle_send_draft()

        handler._bad_request.assert_called_once()
        assert "SMTP is not configured" in str(handler._bad_request.call_args[0][0])

    def test_happy_path_sends_via_smtp_and_re_queues(self, single_db: str) -> None:
        _seed_draft_record(
            single_db,
            "happy-send",
            sender="sender@x.com",
            subject="Hello",
            draft_text="This is the reply.",
        )
        handler = self._setup_handler(single_db, "happy-send")

        with (
            mock.patch("robotsix_auto_mail.smtp.SmtpClient") as mock_smtp_cls,
            mock.patch(
                "robotsix_auto_mail.db.update_sent_reply_text"
            ) as mock_update_sent,
            mock.patch(
                "robotsix_auto_mail.triage.delete_triage_decision"
            ) as mock_delete,
        ):
            mock_client = mock_smtp_cls.return_value.__enter__.return_value
            handler._handle_send_draft()

        # SMTP client was used.
        mock_smtp_cls.assert_called_once()
        mock_client.send.assert_called_once()
        send_kwargs = mock_client.send.call_args[1]
        assert send_kwargs["from_addr"] == "me@example.com"
        assert send_kwargs["to_addr"] == "sender@x.com"
        assert send_kwargs["body"] == "This is the reply."
        assert send_kwargs["cc"] is None  # reply mode, not reply_all

        # Re-queue: update sent reply and delete triage decision.
        mock_update_sent.assert_called_once_with(
            mock.ANY, "happy-send", "This is the reply."
        )
        mock_delete.assert_called_once_with(mock.ANY, "happy-send")

    def test_subject_prepends_re_when_missing(self, single_db: str) -> None:
        _seed_draft_record(
            single_db,
            "subj-missing",
            sender="sender@x.com",
            subject="Hello",
            draft_text="Reply text",
        )
        handler = self._setup_handler(single_db, "subj-missing")

        with (
            mock.patch("robotsix_auto_mail.smtp.SmtpClient") as mock_smtp_cls,
            mock.patch("robotsix_auto_mail.db.update_sent_reply_text"),
            mock.patch("robotsix_auto_mail.triage.delete_triage_decision"),
        ):
            mock_client = mock_smtp_cls.return_value.__enter__.return_value
            handler._handle_send_draft()

        assert mock_client.send.call_args[1]["subject"] == "Re: Hello"

    def test_subject_does_not_double_prepend_re(self, single_db: str) -> None:
        _seed_draft_record(
            single_db,
            "subj-already",
            sender="sender@x.com",
            subject="Re: Hello",
            draft_text="Reply text",
        )
        handler = self._setup_handler(single_db, "subj-already")

        with (
            mock.patch("robotsix_auto_mail.smtp.SmtpClient") as mock_smtp_cls,
            mock.patch("robotsix_auto_mail.db.update_sent_reply_text"),
            mock.patch("robotsix_auto_mail.triage.delete_triage_decision"),
        ):
            mock_client = mock_smtp_cls.return_value.__enter__.return_value
            handler._handle_send_draft()

        assert mock_client.send.call_args[1]["subject"] == "Re: Hello"

    def test_reply_all_includes_cc(self, single_db: str) -> None:
        _seed_draft_record(
            single_db,
            "reply-all",
            sender="sender@x.com",
            subject="Group thread",
            draft_text="Reply all text",
            recipients_json=json.dumps(
                {"to": ["me@example.com", "colleague@x.com"], "cc": ["boss@x.com"]}
            ),
        )
        handler = self._setup_handler(single_db, "reply-all", reply_mode="reply_all")

        with (
            mock.patch("robotsix_auto_mail.smtp.SmtpClient") as mock_smtp_cls,
            mock.patch("robotsix_auto_mail.db.update_sent_reply_text"),
            mock.patch("robotsix_auto_mail.triage.delete_triage_decision"),
        ):
            mock_client = mock_smtp_cls.return_value.__enter__.return_value
            handler._handle_send_draft()

        # self (me@example.com) and sender (sender@x.com) excluded from cc.
        send_kwargs = mock_client.send.call_args[1]
        assert send_kwargs["cc"] == ["colleague@x.com", "boss@x.com"]

    def test_forward_sends_to_forward_to_address(self, single_db: str) -> None:
        _seed_draft_record(
            single_db,
            "forward-ok",
            sender="sender@x.com",
            subject="Interesting thread",
            draft_text="FYI.",
        )
        handler = _DraftMixinFakeHandler(
            single_db,
            mail_config=MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="me@example.com",
                password="s3cret",
            ),
        )
        handler.headers.get.return_value = 200
        handler.rfile.read.return_value = (
            b"message_id=forward-ok&reply_mode=forward"
            b"&forward_to=third@external.com&redirect_to=/board"
        )

        with (
            mock.patch("robotsix_auto_mail.smtp.SmtpClient") as mock_smtp_cls,
            mock.patch("robotsix_auto_mail.db.update_sent_reply_text"),
            mock.patch("robotsix_auto_mail.triage.delete_triage_decision"),
        ):
            mock_client = mock_smtp_cls.return_value.__enter__.return_value
            handler._handle_send_draft()

        send_kwargs = mock_client.send.call_args[1]
        assert send_kwargs["to_addr"] == "third@external.com"
        assert send_kwargs["subject"].startswith("Fwd: ")
        assert send_kwargs["in_reply_to"] is None
        assert send_kwargs["references"] is None

    def test_forward_missing_forward_to_returns_400(self, single_db: str) -> None:
        _seed_draft_record(
            single_db,
            "forward-missing",
            sender="sender@x.com",
            subject="Test",
            draft_text="Some draft",
        )
        handler = _DraftMixinFakeHandler(
            single_db,
            mail_config=MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="me@example.com",
                password="s3cret",
            ),
        )
        handler.headers.get.return_value = 200
        handler.rfile.read.return_value = (
            b"message_id=forward-missing&reply_mode=forward"
            b"&forward_to=&redirect_to=/board"
        )

        with (
            mock.patch("robotsix_auto_mail.db.update_sent_reply_text"),
            mock.patch("robotsix_auto_mail.triage.delete_triage_decision"),
            mock.patch("robotsix_auto_mail.smtp.SmtpClient"),
        ):
            handler._handle_send_draft()

        handler._bad_request.assert_called_once()
        assert "forward_to is required" in str(handler._bad_request.call_args[0][0])

    def test_forward_subject_already_fwd_not_double_prefixed(
        self, single_db: str
    ) -> None:
        _seed_draft_record(
            single_db,
            "fwd-subj",
            sender="sender@x.com",
            subject="Fwd: Earlier thread",
            draft_text="FYI.",
        )
        handler = _DraftMixinFakeHandler(
            single_db,
            mail_config=MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="me@example.com",
                password="s3cret",
            ),
        )
        handler.headers.get.return_value = 200
        handler.rfile.read.return_value = (
            b"message_id=fwd-subj&reply_mode=forward"
            b"&forward_to=other@example.com&redirect_to=/board"
        )

        with (
            mock.patch("robotsix_auto_mail.smtp.SmtpClient") as mock_smtp_cls,
            mock.patch("robotsix_auto_mail.db.update_sent_reply_text"),
            mock.patch("robotsix_auto_mail.triage.delete_triage_decision"),
        ):
            mock_client = mock_smtp_cls.return_value.__enter__.return_value
            handler._handle_send_draft()

        assert mock_client.send.call_args[1]["subject"] == "Fwd: Earlier thread"

    def test_forward_self_forward_guard_returns_400(self, single_db: str) -> None:
        _seed_draft_record(
            single_db,
            "forward-self",
            sender="sender@x.com",
            subject="Test",
            draft_text="Some draft",
        )
        handler = _DraftMixinFakeHandler(
            single_db,
            mail_config=MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="me@example.com",
                password="s3cret",
            ),
        )
        handler.headers.get.return_value = 200
        handler.rfile.read.return_value = (
            b"message_id=forward-self&reply_mode=forward"
            b"&forward_to=me@example.com&redirect_to=/board"
        )

        with (
            mock.patch("robotsix_auto_mail.db.update_sent_reply_text"),
            mock.patch("robotsix_auto_mail.triage.delete_triage_decision"),
            mock.patch("robotsix_auto_mail.smtp.SmtpClient"),
        ):
            handler._handle_send_draft()

        handler._bad_request.assert_called_once()
        assert "Refusing to forward to your own address" in str(
            handler._bad_request.call_args[0][0]
        )
