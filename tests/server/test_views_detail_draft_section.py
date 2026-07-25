"""Unit tests for _render_draft_section."""

from __future__ import annotations

from robotsix_auto_mail.server.views.detail import _render_draft_section

from ._view_helpers import _make_record


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
