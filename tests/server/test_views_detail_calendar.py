"""Unit tests for _render_calendar_feedback."""

from __future__ import annotations

from robotsix_auto_mail.server.views.detail import _render_calendar_feedback

from ._view_helpers import _make_record


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
