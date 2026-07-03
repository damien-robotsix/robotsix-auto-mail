"""Unit tests for ``_compute_reply_all_cc`` and ``_handle_save_draft``.

Drives the mixin directly against a mock handler *self*, isolating the
logic from the HTTP transport.
"""

from __future__ import annotations

import json
from unittest import mock

from tests.server._test_helpers import _DraftMixinFakeHandler
from tests.server.conftest import _populate_db, _seed_triage_decision

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import MailRecord
from robotsix_auto_mail.server._draft_mixin import _compute_reply_all_cc

# ===================================================================
# _compute_reply_all_cc
# ===================================================================


class TestComputeReplyAllCc:
    """Tests for the pure ``_compute_reply_all_cc`` helper."""

    def test_reply_all_picks_to_and_cc_excluding_self_and_sender(self) -> None:
        record = MailRecord(
            message_id="<test@x.com>",
            sender="sender@example.com",
            subject="Test",
            date="2025-01-01T00:00:00",
            recipients_json=json.dumps(
                {"to": ["user@x.com", "other@x.com"], "cc": ["cc1@x.com", "user@x.com"]}
            ),
        )
        result = _compute_reply_all_cc(record, "user@x.com")
        # user@x.com excluded (self), sender@example.com excluded (sender),
        # user@x.com in cc deduped.
        assert result == ["other@x.com", "cc1@x.com"]

    def test_dedup_across_to_and_cc(self) -> None:
        record = MailRecord(
            message_id="<test@x.com>",
            sender="sender@example.com",
            subject="Test",
            date="2025-01-01T00:00:00",
            recipients_json=json.dumps(
                {"to": ["a@x.com", "a@x.com", "b@x.com"], "cc": ["b@x.com", "c@x.com"]}
            ),
        )
        result = _compute_reply_all_cc(record, "user@x.com")
        assert result == ["a@x.com", "b@x.com", "c@x.com"]

    def test_no_cc_returns_none_when_only_self_and_sender(self) -> None:
        record = MailRecord(
            message_id="<test@x.com>",
            sender="sender@example.com",
            subject="Test",
            date="2025-01-01T00:00:00",
            recipients_json=json.dumps({"to": ["user@x.com"], "cc": []}),
        )
        result = _compute_reply_all_cc(record, "user@x.com")
        assert result is None

    def test_malformed_json_returns_none(self) -> None:
        record = MailRecord(
            message_id="<test@x.com>",
            sender="sender@example.com",
            subject="Test",
            date="2025-01-01T00:00:00",
            recipients_json="not valid json",
        )
        result = _compute_reply_all_cc(record, "user@x.com")
        assert result is None

    def test_empty_recipients_returns_none(self) -> None:
        record = MailRecord(
            message_id="<test@x.com>",
            sender="sender@example.com",
            subject="Test",
            date="2025-01-01T00:00:00",
            recipients_json=json.dumps({"to": [], "cc": []}),
        )
        result = _compute_reply_all_cc(record, "user@x.com")
        assert result is None

    def test_case_insensitive_self_exclusion(self) -> None:
        record = MailRecord(
            message_id="<test@x.com>",
            sender="sender@example.com",
            subject="Test",
            date="2025-01-01T00:00:00",
            recipients_json=json.dumps({"to": ["Sender@Example.com", "other@x.com"]}),
        )
        result = _compute_reply_all_cc(record, "user@x.com")
        # Sender@Example.com excluded (matches record.sender case-insensitively).
        assert result == ["other@x.com"]

    def test_case_insensitive_from_exclusion(self) -> None:
        record = MailRecord(
            message_id="<test@x.com>",
            sender="sender@example.com",
            subject="Test",
            date="2025-01-01T00:00:00",
            recipients_json=json.dumps({"to": ["sender@EXAMPLE.com", "other@x.com"]}),
        )
        result = _compute_reply_all_cc(record, "User@X.com")
        # sender@EXAMPLE.com excluded (matches record.sender).
        assert result == ["other@x.com"]

    def test_non_string_recipients_skipped(self) -> None:
        record = MailRecord(
            message_id="<test@x.com>",
            sender="sender@example.com",
            subject="Test",
            date="2025-01-01T00:00:00",
            recipients_json=json.dumps({"to": [None, 123, "valid@x.com"]}),
        )
        result = _compute_reply_all_cc(record, "user@x.com")
        assert result == ["valid@x.com"]

    def test_non_dict_recipients_json_returns_none(self) -> None:
        record = MailRecord(
            message_id="<test@x.com>",
            sender="sender@example.com",
            subject="Test",
            date="2025-01-01T00:00:00",
            recipients_json=json.dumps([1, 2, 3]),
        )
        result = _compute_reply_all_cc(record, "user@x.com")
        assert result is None


# ===================================================================
# _handle_save_draft
# ===================================================================


class TestHandleSaveDraft:
    """Tests for ``_handle_save_draft``."""

    def _setup_handler(self, db_path: str, message_id: str) -> _DraftMixinFakeHandler:
        """Create a _DraftMixinFakeHandler with form data for /save-draft."""
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
            f"message_id={message_id}&draft_text=Hello+world&redirect_to=/board"
        ).encode("utf-8")
        return handler

    def test_persists_draft_text(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "save-me",
                    "sender": "x@x.com",
                    "subject": "Save test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = self._setup_handler(single_db, "save-me")

        with (
            mock.patch("robotsix_auto_mail.db.update_draft_text") as mock_update,
            mock.patch(
                "robotsix_auto_mail.server._draft_mixin.get_triage_decision",
                return_value=None,
            ),
            mock.patch("robotsix_auto_mail.server._draft_mixin.set_triage_decision"),
            mock.patch("robotsix_auto_mail.server._draft_mixin.record_user_action"),
        ):
            handler._handle_save_draft()

        mock_update.assert_called_once()
        assert mock_update.call_args[0][1] == "save-me"
        assert mock_update.call_args[0][2] == "Hello world"

    def test_sets_draft_ready_when_no_current_decision(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "no-decision",
                    "sender": "x@x.com",
                    "subject": "No decision",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = self._setup_handler(single_db, "no-decision")

        with (
            mock.patch("robotsix_auto_mail.db.update_draft_text"),
            mock.patch(
                "robotsix_auto_mail.server._draft_mixin.get_triage_decision",
                return_value=None,
            ),
            mock.patch(
                "robotsix_auto_mail.server._draft_mixin.set_triage_decision"
            ) as mock_set,
            mock.patch(
                "robotsix_auto_mail.server._draft_mixin.record_user_action"
            ) as mock_record,
        ):
            handler._handle_save_draft()

        mock_set.assert_called_once_with(
            mock.ANY, "no-decision", "DRAFT_READY", source="user", reason="draft saved"
        )
        mock_record.assert_called_once_with(mock.ANY, "DRAFT_READY", config=mock.ANY)

    def test_sets_draft_ready_when_current_action_is_not_draft_ready(
        self, single_db: str
    ) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "other-action",
                    "sender": "x@x.com",
                    "subject": "Other action",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        # Seed a triage decision with a different action.
        _seed_triage_decision(single_db, "other-action", action="TO_ANSWER")
        handler = self._setup_handler(single_db, "other-action")

        with (
            mock.patch("robotsix_auto_mail.db.update_draft_text"),
            # Let get_triage_decision hit the real DB so it returns TO_ANSWER.
            mock.patch(
                "robotsix_auto_mail.server._draft_mixin.set_triage_decision"
            ) as mock_set,
            mock.patch(
                "robotsix_auto_mail.server._draft_mixin.record_user_action"
            ) as mock_record,
        ):
            handler._handle_save_draft()

        # Should still transition because current action is TO_ANSWER, not DRAFT_READY.
        mock_set.assert_called_once_with(
            mock.ANY, "other-action", "DRAFT_READY", source="user", reason="draft saved"
        )
        mock_record.assert_called_once()

    def test_does_not_set_draft_ready_when_already_draft_ready(
        self, single_db: str
    ) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "already-dr",
                    "sender": "x@x.com",
                    "subject": "Already DR",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(single_db, "already-dr", action="DRAFT_READY")
        handler = self._setup_handler(single_db, "already-dr")

        with (
            mock.patch("robotsix_auto_mail.db.update_draft_text"),
            mock.patch(
                "robotsix_auto_mail.server._draft_mixin.set_triage_decision"
            ) as mock_set,
            mock.patch(
                "robotsix_auto_mail.server._draft_mixin.record_user_action"
            ) as mock_record,
        ):
            handler._handle_save_draft()

        # No new triage decision or user action should be recorded.
        mock_set.assert_not_called()
        mock_record.assert_not_called()
