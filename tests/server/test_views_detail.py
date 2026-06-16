"""Unit tests for src/robotsix_auto_mail/server/views/detail.py."""

from __future__ import annotations

from unittest import mock

from robotsix_auto_mail.db import MailRecord
from robotsix_auto_mail.server.views.detail import (
    _build_detail_html,
    _render_attachments,
    _render_body,
    _render_calendar_feedback,
    _render_draft_section,
    _render_imap_uid_section,
    _render_recipients,
    _render_triage_section,
)
from robotsix_auto_mail.triage import TriageDecision

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(**overrides) -> MailRecord:
    """Create a minimal MailRecord with sensible defaults."""
    defaults = {
        "message_id": "<test@example.com>",
        "sender": "sender@example.com",
        "subject": "Test Subject",
        "date": "2025-01-15T10:30:00",
        "body_plain": "Hello world",
        "body_html": "",
    }
    defaults.update(overrides)
    return MailRecord(**defaults)


# ---------------------------------------------------------------------------
# _build_detail_html
# ---------------------------------------------------------------------------


class TestBuildDetailHtml:
    # _build_detail_html imports *inside* the function:
    #   from robotsix_auto_mail.db import get_record_by_message_id, init_db
    # and uses the *module-level* import of get_triage_decision from
    #   from robotsix_auto_mail.triage import get_triage_decision
    # So we patch the db module (for the local import) and the detail
    # module itself (for the already-imported get_triage_decision).
    _PATCH_TRIG = "robotsix_auto_mail.server.views.detail.get_triage_decision"

    def test_valid_record_returns_full_html(self):
        record = _make_record()
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch(
                "robotsix_auto_mail.db.init_db", return_value=fake_conn
            ) as mock_init,
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ) as mock_get,
            mock.patch(
                self._PATCH_TRIG,
                return_value=decision,
            ) as mock_triage,
        ):
            result = _build_detail_html(":memory:", record.message_id)

        assert result is not None
        assert "<!DOCTYPE html>" in result
        assert "<title>Mail: Test Subject</title>" in result
        assert "sender@example.com" in result
        assert "← Back to board" in result
        mock_init.assert_called_once_with(":memory:", skip_migrations=True)
        mock_get.assert_called_once_with(fake_conn, record.message_id)
        mock_triage.assert_called_once_with(fake_conn, record.message_id)

    def test_embed_true_returns_fragment(self):
        record = _make_record()
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch("robotsix_auto_mail.db.init_db", return_value=fake_conn),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(
                self._PATCH_TRIG,
                return_value=decision,
            ),
        ):
            result = _build_detail_html(":memory:", record.message_id, embed=True)

        assert result is not None
        assert "<!DOCTYPE html>" not in result
        assert '<link rel="stylesheet" href="/static/automail/board.css">' in result
        assert "refreshBoard" in result

    def test_record_none_returns_none(self):
        fake_conn = mock.Mock()

        with (
            mock.patch("robotsix_auto_mail.db.init_db", return_value=fake_conn),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=None,
            ),
            mock.patch(
                self._PATCH_TRIG,
                return_value=None,
            ),
        ):
            result = _build_detail_html(":memory:", "missing@id")

        assert result is None

    def test_malformed_recipients_json_graceful_fallback(self):
        record = _make_record(
            recipients_json="{invalid json",
            attachments_json='[{"filename": "a.pdf"}]',
        )
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch("robotsix_auto_mail.db.init_db", return_value=fake_conn),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(
                self._PATCH_TRIG,
                return_value=decision,
            ),
        ):
            result = _build_detail_html(":memory:", record.message_id)

        assert result is not None
        # Malformed recipients → fallback to empty, so "(none)" for To
        assert "<em>(none)</em>" in result

    def test_malformed_attachments_json_graceful_fallback(self):
        record = _make_record(
            recipients_json='{"to": ["a@b.com"], "cc": []}',
            attachments_json="{invalid",
        )
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch("robotsix_auto_mail.db.init_db", return_value=fake_conn),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(
                self._PATCH_TRIG,
                return_value=decision,
            ),
        ):
            result = _build_detail_html(":memory:", record.message_id)

        assert result is not None
        # Malformed attachments → fallback to "(none)"
        assert "<em>(none)</em>" in result

    def test_no_subject_shows_placeholder_in_title(self):
        record = _make_record(subject="")
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch("robotsix_auto_mail.db.init_db", return_value=fake_conn),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(
                self._PATCH_TRIG,
                return_value=decision,
            ),
        ):
            result = _build_detail_html(":memory:", record.message_id)

        assert result is not None
        assert "(no subject)" in result

    def test_triage_decision_none_shows_placeholder(self):
        record = _make_record()
        fake_conn = mock.Mock()

        with (
            mock.patch("robotsix_auto_mail.db.init_db", return_value=fake_conn),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(
                self._PATCH_TRIG,
                return_value=None,
            ),
        ):
            result = _build_detail_html(":memory:", record.message_id)

        assert result is not None
        assert "(no triage decision)" in result

    # -- account-aware output tests ----------------------------------------

    def test_legacy_no_account_has_plain_move_action(self):
        """Without *current_account_id*, the move form action is ``/move``."""
        record = _make_record()
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch("robotsix_auto_mail.db.init_db", return_value=fake_conn),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(self._PATCH_TRIG, return_value=decision),
        ):
            result = _build_detail_html(":memory:", record.message_id)

        assert result is not None
        assert 'action="/move"' in result
        assert "?account=" not in result

    def test_real_account_adds_query_to_move_action(self):
        """A real *current_account_id* adds ``?account=<id>`` to the form."""
        record = _make_record()
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch("robotsix_auto_mail.db.init_db", return_value=fake_conn),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(self._PATCH_TRIG, return_value=decision),
        ):
            result = _build_detail_html(
                ":memory:", record.message_id, current_account_id="acct-42"
            )

        assert result is not None
        assert 'action="/move?account=acct-42"' in result

    def test_embed_with_account_adds_account_to_redirect(self):
        """Embed mode with *current_account_id* carries ``&account=<id>``."""
        record = _make_record()
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch("robotsix_auto_mail.db.init_db", return_value=fake_conn),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(self._PATCH_TRIG, return_value=decision),
        ):
            result = _build_detail_html(
                ":memory:",
                record.message_id,
                embed=True,
                current_account_id="acct-42",
            )

        assert result is not None
        assert 'name="redirect_to"' in result
        assert "&account=acct-42" in result
        # The redirect URL starts with /email/...?embed=1 and ends with the
        # account suffix.
        assert '"/email/' in result

    def test_embed_without_account_omits_account_from_redirect(self):
        """Embed mode without *current_account_id* omits ``&account=``."""
        record = _make_record()
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch("robotsix_auto_mail.db.init_db", return_value=fake_conn),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(self._PATCH_TRIG, return_value=decision),
        ):
            result = _build_detail_html(":memory:", record.message_id, embed=True)

        assert result is not None
        assert 'name="redirect_to"' in result
        assert "&account=" not in result

    def test_aggregate_sentinel_omits_account(self):
        """``current_account_id="__all__"`` is treated as no-account."""
        record = _make_record()
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch("robotsix_auto_mail.db.init_db", return_value=fake_conn),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(self._PATCH_TRIG, return_value=decision),
        ):
            result = _build_detail_html(
                ":memory:",
                record.message_id,
                embed=True,
                current_account_id="__all__",
            )

        assert result is not None
        assert 'action="/move"' in result
        assert "?account=" not in result
        assert "&account=" not in result


# ---------------------------------------------------------------------------
# _render_body
# ---------------------------------------------------------------------------


class TestRenderBody:
    def test_no_body(self):
        record = _make_record(body_plain="", body_html="")
        body_html, body_note = _render_body(record)
        assert "(no body)" in body_html
        assert body_note == ""

    def test_html_derived_body(self):
        record = _make_record(
            body_plain="",
            body_html="<p>Hello</p>",
        )
        body_html, body_note = _render_body(record)
        assert "<pre>" in body_html
        assert "(from HTML)" in body_html
        assert "HTML version available" in body_note

    def test_plain_text_body(self):
        record = _make_record(
            body_plain="Hello world",
            body_html="",
        )
        body_html, body_note = _render_body(record)
        assert "<pre>Hello world</pre>" in body_html
        assert "(from HTML)" not in body_html
        assert body_note == ""

    def test_body_with_html_version_note(self):
        record = _make_record(
            body_plain="Hello",
            body_html="<p>Hello</p>",
        )
        _body_html, body_note = _render_body(record)
        assert "HTML version available" in body_note


# ---------------------------------------------------------------------------
# _render_draft_section
# ---------------------------------------------------------------------------


class TestRenderDraftSection:
    def test_to_answer_shows_save_and_generate_forms_no_send(self):
        record = _make_record()
        result = _render_draft_section(record, "TO_ANSWER", False, "")
        assert "Save draft" in result
        assert "Generate with AI" in result
        assert "Reply &amp; archive" not in result

    def test_draft_ready_shows_send_forms(self):
        record = _make_record(draft_text="My draft")
        result = _render_draft_section(record, "DRAFT_READY", False, "")
        assert "Update draft" in result
        assert "Regenerate with AI" in result
        assert "Reply &amp; archive" in result
        assert 'value="reply"' in result
        assert 'value="reply_all"' in result
        assert "My draft" in result

    def test_unrelated_action_without_focus_returns_empty(self):
        record = _make_record()
        result = _render_draft_section(record, "INBOX", False, "")
        assert result == ""

    def test_unrelated_action_with_focus_draft_shows_form(self):
        record = _make_record()
        result = _render_draft_section(record, "INBOX", True, "")
        assert "Save draft" in result


# ---------------------------------------------------------------------------
# _render_recipients
# ---------------------------------------------------------------------------


class TestRenderRecipients:
    def test_empty_to_and_cc(self):
        to_html, cc_section = _render_recipients([], [])
        assert to_html == "<em>(none)</em>"
        assert cc_section == ""

    def test_to_with_recipients(self):
        to_html, cc_section = _render_recipients(["a@b.com", "c@d.com"], [])
        assert "a@b.com" in to_html
        assert "c@d.com" in to_html
        assert cc_section == ""

    def test_cc_with_recipients(self):
        to_html, cc_section = _render_recipients(
            ["to@b.com"], ["cc1@b.com", "cc2@b.com"]
        )
        assert "to@b.com" in to_html
        assert "cc1@b.com" in cc_section
        assert "cc2@b.com" in cc_section
        assert "CC" in cc_section

    def test_empty_to_with_cc(self):
        to_html, cc_section = _render_recipients([], ["cc@b.com"])
        assert to_html == "<em>(none)</em>"
        assert "cc@b.com" in cc_section


# ---------------------------------------------------------------------------
# _render_attachments
# ---------------------------------------------------------------------------


class TestRenderAttachments:
    def test_empty_list(self):
        result = _render_attachments([])
        assert result == "<em>(none)</em>"

    def test_dict_items_without_size(self):
        result = _render_attachments(
            [{"filename": "report.pdf"}, {"filename": "image.png"}]
        )
        assert "report.pdf" in result
        assert "image.png" in result

    def test_dict_items_with_size(self):
        result = _render_attachments([{"filename": "big.zip", "size": 1_048_576}])
        assert "big.zip" in result
        assert "1,048,576 bytes" in result

    def test_dict_items_with_size_zero(self):
        result = _render_attachments([{"filename": "empty.txt", "size": 0}])
        assert "empty.txt" in result
        assert "0 bytes" in result

    def test_non_dict_items(self):
        result = _render_attachments(["just_a_string.pdf"])
        assert "just_a_string.pdf" in result

    def test_mixed_items(self):
        result = _render_attachments(
            [
                {"filename": "a.pdf", "size": 100},
                "b.txt",
                {"filename": "c.doc"},
            ]
        )
        assert "a.pdf" in result
        assert "100 bytes" in result
        assert "b.txt" in result
        assert "c.doc" in result


# ---------------------------------------------------------------------------
# _render_imap_uid_section
# ---------------------------------------------------------------------------


class TestRenderImapUidSection:
    def test_none_uid_returns_empty(self):
        record = _make_record(imap_uid=None)
        result = _render_imap_uid_section(record)
        assert result == ""

    def test_present_uid_shows_section(self):
        record = _make_record(imap_uid=42)
        result = _render_imap_uid_section(record)
        assert "IMAP UID" in result
        assert "42" in result


# ---------------------------------------------------------------------------
# _render_triage_section
# ---------------------------------------------------------------------------


class TestRenderTriageSection:
    def test_none_decision_shows_placeholder(self):
        result = _render_triage_section(None)
        assert "(no triage decision)" in result

    def test_decision_without_reason(self):
        decision = TriageDecision(
            message_id="<test@example.com>",
            action="TO_ANSWER",
            source="agent",
            confidence="high",
        )
        result = _render_triage_section(decision)
        assert "TO_ANSWER" in result
        assert "agent" in result
        assert "high" in result
        assert "triage-reason" not in result

    def test_decision_with_reason(self):
        decision = TriageDecision(
            message_id="<test@example.com>",
            action="TO_DELETE",
            source="agent",
            reason="Spam detected",
            confidence="medium",
        )
        result = _render_triage_section(decision)
        assert "TO_DELETE" in result
        assert "Spam detected" in result
        assert "triage-reason" in result


# ---------------------------------------------------------------------------
# _render_calendar_feedback
# ---------------------------------------------------------------------------


class TestRenderCalendarFeedback:
    def test_returns_empty_when_no_event_ref(self):
        record = _make_record(calendar_event_ref="")
        result = _render_calendar_feedback(record)
        assert result == ""

    def test_returns_success_when_event_ref_is_link(self):
        record = _make_record(calendar_event_ref="https://calendar.example.com/event/1")
        result = _render_calendar_feedback(record)
        assert "calendar-feedback calendar-success" in result
        assert "https://calendar.example.com/event/1" in result
        assert "\u2705" in result

    def test_returns_error_when_event_ref_starts_with_error(self):
        record = _make_record(calendar_event_ref="error: Dispatch failed")
        result = _render_calendar_feedback(record)
        assert "calendar-feedback calendar-error" in result
        assert "Dispatch failed" in result
        assert "\u26a0\ufe0f" in result

    def test_error_with_empty_message_shows_unknown(self):
        record = _make_record(calendar_event_ref="error: ")
        result = _render_calendar_feedback(record)
        assert "Unknown error" in result

    def test_includes_calendar_detail_field_wrapper(self):
        record = _make_record(calendar_event_ref="https://cal.example.com/ev/2")
        result = _render_calendar_feedback(record)
        assert '<div class="detail-field">' in result
        assert '<div class="detail-label">Calendar</div>' in result