"""Unit tests for src/robotsix_auto_mail/server/views/forms.py."""

from __future__ import annotations

from robotsix_auto_mail.db import MailRecord
from robotsix_auto_mail.server.views.forms import _render_move_form
from robotsix_auto_mail.triage import TRIAGE_ACTION_LABELS, TRIAGE_ACTION_ORDER

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
# _render_move_form
# ---------------------------------------------------------------------------


class TestRenderMoveForm:
    """Unit tests for ``_render_move_form``."""

    # -- form structure ----------------------------------------------------

    def test_renders_form_with_correct_action_and_method(self):
        record = _make_record()
        result = _render_move_form(
            record, "INBOX", '<input type="hidden" name="x" value="1">'
        )
        assert '<form class="detail-form" method="post" action="/move">' in result
        assert "</form>" in result

    def test_renders_select_with_correct_name(self):
        record = _make_record()
        result = _render_move_form(record, "INBOX", "")
        assert '<select name="triage_action">' in result
        assert "</select>" in result

    def test_renders_submit_button(self):
        record = _make_record()
        result = _render_move_form(record, "INBOX", "")
        assert '<button type="submit">Move</button>' in result

    def test_includes_hidden_message_id_input(self):
        record = _make_record(message_id="<abc123@example.com>")
        result = _render_move_form(record, "INBOX", "")
        assert '<input type="hidden" name="message_id"' in result
        # message_id is HTML-escaped
        assert "&lt;abc123@example.com&gt;" in result

    def test_includes_redirect_input_verbatim(self):
        record = _make_record()
        redirect = '<input type="hidden" name="redirect_to" value="/foo">'
        result = _render_move_form(record, "INBOX", redirect)
        assert redirect in result

    # -- option rendering --------------------------------------------------

    def test_all_actions_present_as_options(self):
        record = _make_record()
        result = _render_move_form(record, "INBOX", "")
        for action in TRIAGE_ACTION_ORDER:
            assert f'value="{action}"' in result
            assert TRIAGE_ACTION_LABELS[action] in result

    def test_selected_attribute_on_current_action_inbox(self):
        record = _make_record()
        result = _render_move_form(record, "INBOX", "")
        assert '<option value="INBOX" selected>Inbox</option>' in result

    def test_selected_attribute_on_current_action_to_archive(self):
        record = _make_record()
        result = _render_move_form(record, "TO_ARCHIVE", "")
        assert '<option value="TO_ARCHIVE" selected>To archive</option>' in result

    def test_selected_attribute_on_current_action_to_delete(self):
        record = _make_record()
        result = _render_move_form(record, "TO_DELETE", "")
        assert '<option value="TO_DELETE" selected>To delete</option>' in result

    def test_selected_attribute_on_current_action_draft_ready(self):
        record = _make_record()
        result = _render_move_form(record, "DRAFT_READY", "")
        assert '<option value="DRAFT_READY" selected>Draft ready</option>' in result

    def test_no_other_option_has_selected_when_one_is_selected(self):
        record = _make_record()
        result = _render_move_form(record, "TO_ANSWER", "")
        # Count occurrences of " selected" — should be exactly 1
        assert result.count(" selected") == 1

    # -- account_id query string -------------------------------------------

    def test_account_id_none_omits_query_string(self):
        record = _make_record()
        result = _render_move_form(record, "INBOX", "", account_id=None)
        assert 'action="/move"' in result
        assert "?account=" not in result

    def test_account_id_all_sentinel_omits_query_string(self):
        record = _make_record()
        result = _render_move_form(record, "INBOX", "", account_id="__all__")
        assert 'action="/move"' in result
        assert "?account=" not in result

    def test_real_account_id_adds_query_string(self):
        record = _make_record()
        result = _render_move_form(record, "INBOX", "", account_id="acct-42")
        assert 'action="/move?account=acct-42"' in result

    def test_account_id_with_special_chars_is_url_encoded(self):
        record = _make_record()
        result = _render_move_form(record, "INBOX", "", account_id="user@domain.com")
        # @ should be percent-encoded by quote(..., safe="")
        assert 'action="/move?account=user%40domain.com"' in result

    # -- HTML escaping -----------------------------------------------------

    def test_message_id_with_angle_brackets_is_escaped(self):
        record = _make_record(message_id='<script>alert("xss")</script>')
        result = _render_move_form(record, "INBOX", "")
        # The raw angle brackets and quotes should be HTML-escaped
        assert "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;" in result
        # The raw script tag must NOT appear unescaped
        assert "<script>" not in result

    def test_message_id_with_ampersand_is_escaped(self):
        record = _make_record(message_id="a&b")
        result = _render_move_form(record, "INBOX", "")
        assert "a&amp;b" in result

    def test_action_labels_are_escaped(self):
        # TRIAGE_ACTION_LABELS are hardcoded safe strings, but the
        # escaping is applied — verify it doesn't corrupt output.
        record = _make_record()
        result = _render_move_form(record, "INBOX", "")
        # Every label should appear as-is (no double-escaping)
        for label in TRIAGE_ACTION_LABELS.values():
            assert label in result
