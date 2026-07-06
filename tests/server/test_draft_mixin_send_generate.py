"""Unit tests for ``_handle_send_draft``, ``_handle_generate_draft``, and
``_redirect_generate_draft``.

Drives the mixin directly against a mock handler *self*, isolating the
logic from the HTTP transport and covering branches that integration
tests miss (ImportError degradation, DraftGenerationError swallowing,
self-reply guard, empty-draft guard, reply_mode validation, etc.).
"""

from __future__ import annotations

import json
import sys
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.server._draft_generator import DraftResult
from tests.server._test_helpers import _DraftMixinFakeHandler
from tests.server.conftest import _populate_db, _seed_draft_record

# ===================================================================
# _handle_send_draft
# ===================================================================


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


# ===================================================================
# _handle_generate_draft
# ===================================================================


class TestHandleGenerateDraft:
    """Tests for ``_handle_generate_draft``."""

    def _setup_handler(
        self, db_path: str, message_id: str, redirect_to: str = "/board"
    ) -> _DraftMixinFakeHandler:
        handler = _DraftMixinFakeHandler(
            db_path,
            mail_config=MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="me@example.com",
                password="s3cret",
                llm_api_key="sk-test",
            ),
        )
        handler.headers.get.return_value = 200
        handler.rfile.read.return_value = (
            f"message_id={message_id}&redirect_to={redirect_to}"
        ).encode("utf-8")
        return handler

    def test_import_error_degradation(self, tmp_db_path: str) -> None:
        """When robotsix_auto_mail.server._draft_generator is not importable, redirect gracefully.

        Uses ``mock.patch.dict`` to set the module entry to ``None`` in
        ``sys.modules``, which forces Python to raise ``ImportError``
        (as if the optional extra were not installed) rather than
        attempting a re-import that would likely succeed because its
        dependencies are core packages.
        """
        handler = self._setup_handler(tmp_db_path, "any-id")
        handler._redirect_generate_draft = mock.MagicMock()

        with (
            mock.patch.dict(
                sys.modules, {"robotsix_auto_mail.server._draft_generator": None}
            ),
            mock.patch(
                "robotsix_auto_mail.server._draft_mixin.set_triage_decision"
            ) as mock_set,
            mock.patch("robotsix_auto_mail.server._constants.init_db") as mock_init_db,
        ):
            handler._handle_generate_draft()

        # The ImportError path must redirect but NOT open a DB connection
        # or set a triage decision (both happen only after a successful
        # draft import).
        handler._redirect_generate_draft.assert_called_once_with("any-id", "/board")
        mock_init_db.assert_not_called()
        mock_set.assert_not_called()

    def test_draft_generation_error_swallowed(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "gen-err",
                    "sender": "x@x.com",
                    "subject": "Test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = self._setup_handler(single_db, "gen-err")
        handler._redirect_generate_draft = mock.MagicMock()

        from robotsix_auto_mail.server._draft_generator import DraftGenerationError

        with (
            mock.patch(
                "robotsix_auto_mail.server._draft_generator.generate_draft_reply",
                side_effect=DraftGenerationError("LLM unavailable"),
            ),
            mock.patch(
                "robotsix_auto_mail.server._draft_mixin.set_triage_decision"
            ) as mock_set,
        ):
            handler._handle_generate_draft()

        # set_triage_decision should NOT be called (error was swallowed).
        mock_set.assert_not_called()
        # Still redirects.
        handler._redirect_generate_draft.assert_called_once_with("gen-err", "/board")

    def test_success_sets_draft_ready(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "gen-ok",
                    "sender": "x@x.com",
                    "subject": "Test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = self._setup_handler(single_db, "gen-ok")
        handler._redirect_generate_draft = mock.MagicMock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._draft_generator.generate_draft_reply"
            ),
            mock.patch(
                "robotsix_auto_mail.server._draft_mixin.set_triage_decision"
            ) as mock_set,
        ):
            handler._handle_generate_draft()

        mock_set.assert_called_once_with(
            mock.ANY, "gen-ok", "DRAFT_READY", source="user", reason="draft generated"
        )
        handler._redirect_generate_draft.assert_called_once_with("gen-ok", "/board")

    def test_missing_message_id_returns_400(self, tmp_db_path: str) -> None:
        handler = _DraftMixinFakeHandler(tmp_db_path)
        handler.headers.get.return_value = 100
        handler.rfile.read.return_value = b"message_id=&redirect_to=/board"

        handler._handle_generate_draft()
        handler._bad_request.assert_called_once_with("Missing message_id")


# ===================================================================
# _redirect_generate_draft
# ===================================================================


class TestRedirectGenerateDraft:
    """Tests for ``_redirect_generate_draft``."""

    def test_safe_redirect_to_used(self, tmp_db_path: str) -> None:
        handler = _DraftMixinFakeHandler(tmp_db_path)
        handler._redirect_generate_draft("msg-1", "/detail?msg=msg-1")
        handler._redirect.assert_called_once_with("/detail?msg=msg-1", 302)

    def test_unsafe_redirect_to_falls_back_to_board(self, tmp_db_path: str) -> None:
        handler = _DraftMixinFakeHandler(tmp_db_path)
        handler._redirect_generate_draft("msg-2", "//evil.com/phish")
        handler._redirect.assert_called_once_with("/board#msg-2", 302)

    def test_empty_redirect_to_falls_back_to_board(self, tmp_db_path: str) -> None:
        handler = _DraftMixinFakeHandler(tmp_db_path)
        handler._redirect_generate_draft("msg-3", "")
        handler._redirect.assert_called_once_with("/board#msg-3", 302)


# ===================================================================
# Draft generator unit tests (merged from tests/draft/test_draft.py)
# ===================================================================


def _patch_llm(
    result_obj: "DraftResult",
) -> tuple[mock.MagicMock, mock._patch[mock.MagicMock]]:
    """Patch get_provider to return *result_obj* from the LLM.

    Returns the mock handle (to assert ``close()``) and the patcher.
    """

    mock_run_result = mock.MagicMock()
    mock_run_result.output = result_obj
    mock_handle = mock.MagicMock()
    mock_handle.run_sync.return_value = mock_run_result

    mock_provider = mock.MagicMock()
    mock_provider.build_agent.return_value = mock_handle
    mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

    patcher = mock.patch(
        "robotsix_llmio.core.factory.get_provider_for_identifier",
        return_value=mock_provider,
    )
    return mock_handle, patcher


def _insert_inbox(conn: object, message_id: str, **overrides: str) -> None:
    """Insert an inbox MailRecord with sensible defaults."""
    from robotsix_auto_mail.db import MailRecord, insert_record

    record = MailRecord(
        message_id=message_id,
        sender=overrides.get("sender", "alice@example.com"),
        subject=overrides.get("subject", "Hello"),
        date="2025-06-01T12:00:00",
        status=overrides.get("status", "to_read"),
        body_plain=overrides.get("body_plain", "Can we meet next week?"),
        notes=overrides.get("notes", ""),
    )
    insert_record(conn, record)  # type: ignore[arg-type]


class TestDraftGenerator:
    """Tests for the draft generator functions."""

    def test_generate_draft_reply_returns_and_persists(self) -> None:
        """The mocked draft text is returned and persisted to draft_text."""
        from robotsix_auto_mail.db import get_record_by_message_id, init_db
        from robotsix_auto_mail.server._draft_generator import (
            DraftResult,
            generate_draft_reply,
        )

        conn = init_db(":memory:")
        try:
            _insert_inbox(conn, "mid-1")
            mock_handle, patcher = _patch_llm(
                DraftResult(draft_text="Sure, [your availability]. [Your name]")
            )
            with patcher:
                draft = generate_draft_reply(conn, "mid-1", api_key="sk-test")

            assert draft == "Sure, [your availability]. [Your name]"
            record = get_record_by_message_id(conn, "mid-1")
            assert record is not None
            assert record.draft_text == "Sure, [your availability]. [Your name]"
            mock_handle.close.assert_called_once()
        finally:
            conn.close()

    def test_build_draft_user_message_includes_notes(self) -> None:
        """Non-empty notes are appended under a labelled section."""
        from robotsix_auto_mail.db import get_record_by_message_id, init_db
        from robotsix_auto_mail.server._draft_generator import (
            _build_draft_user_message,
        )

        conn = init_db(":memory:")
        try:
            _insert_inbox(conn, "mid-notes", notes="decline politely")
            record = get_record_by_message_id(conn, "mid-notes")
            assert record is not None
            message = _build_draft_user_message(record)
            assert "User notes / instructions" in message
            assert "decline politely" in message
        finally:
            conn.close()

    def test_build_draft_user_message_omits_empty_notes(self) -> None:
        """Empty/whitespace notes produce no notes section."""
        from robotsix_auto_mail.db import get_record_by_message_id, init_db
        from robotsix_auto_mail.server._draft_generator import (
            _build_draft_user_message,
        )

        conn = init_db(":memory:")
        try:
            _insert_inbox(conn, "mid-empty", notes="   ")
            record = get_record_by_message_id(conn, "mid-empty")
            assert record is not None
            message = _build_draft_user_message(record)
            assert "User notes / instructions" not in message
        finally:
            conn.close()

    def test_generate_draft_reply_missing_record_raises(self) -> None:
        """A missing message_id raises DraftGenerationError."""
        from robotsix_auto_mail.db import init_db
        from robotsix_auto_mail.server._draft_generator import (
            DraftGenerationError,
            generate_draft_reply,
        )

        conn = init_db(":memory:")
        try:
            with pytest.raises(DraftGenerationError):
                generate_draft_reply(conn, "does-not-exist", api_key="sk-test")
        finally:
            conn.close()

    def test_build_draft_system_prompt_contains_required_keywords(self) -> None:
        """The system prompt is non-empty and includes key instructions."""
        from robotsix_auto_mail.server._draft_generator import (
            _build_draft_system_prompt,
        )

        prompt = _build_draft_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        # Expected keywords from the prompt rules:
        for keyword in ("LANGUAGE", "draft_text", "placeholder", "professional"):
            assert keyword in prompt, f"Missing keyword in system prompt: {keyword}"

    def test_generate_draft_reply_llm_error_propagates(self) -> None:
        """LLM failure raises DraftGenerationError wrapping the original."""
        from robotsix_auto_mail.db import init_db
        from robotsix_auto_mail.server._draft_generator import (
            DraftGenerationError,
            DraftResult,
            generate_draft_reply,
        )

        conn = init_db(":memory:")
        try:
            _insert_inbox(conn, "mid-err")
            _, patcher = _patch_llm(DraftResult(draft_text="irrelevant"))
            # Make run_agent raise a non-DraftGenerationError exception
            run_agent_patch = mock.patch(
                "robotsix_llmio.core.run_agent",
                side_effect=ValueError("LLM timeout"),
            )
            with patcher, run_agent_patch:
                with pytest.raises(DraftGenerationError, match="LLM timeout"):
                    generate_draft_reply(conn, "mid-err", api_key="sk-test")
        finally:
            conn.close()

    def test_build_draft_user_message_truncates_long_body(self) -> None:
        """A body exceeding _BODY_CHAR_LIMIT is truncated in the user message."""
        from robotsix_auto_mail.db import get_record_by_message_id, init_db
        from robotsix_auto_mail.server._draft_generator import (
            _BODY_CHAR_LIMIT,
            _build_draft_user_message,
        )

        conn = init_db(":memory:")
        try:
            long_body = "x" * (_BODY_CHAR_LIMIT + 500)
            _insert_inbox(conn, "mid-long", body_plain=long_body)
            record = get_record_by_message_id(conn, "mid-long")
            assert record is not None
            message = _build_draft_user_message(record)
            # The truncated body should appear in the message, but the full one
            # should not.
            expected_truncated = long_body[:_BODY_CHAR_LIMIT]
            assert expected_truncated in message
            assert long_body not in message
            # The message must be shorter than the original body + framing
            assert len(message) < len(long_body)
        finally:
            conn.close()

    def test_draft_result_requires_draft_text(self) -> None:
        """DraftResult enforces that draft_text is required."""
        from pydantic import ValidationError

        from robotsix_auto_mail.server._draft_generator import DraftResult

        DraftResult(draft_text="hello")  # valid — should not raise

        with pytest.raises(ValidationError):
            DraftResult()  # type: ignore[call-arg]

        with pytest.raises(ValidationError):
            DraftResult(draft_text=123)  # type: ignore[arg-type]
