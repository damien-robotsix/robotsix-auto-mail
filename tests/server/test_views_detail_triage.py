"""Unit tests for _render_triage_section."""

from __future__ import annotations

from robotsix_auto_mail.server.views.detail import _render_triage_section
from robotsix_auto_mail.triage import TriageDecision


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
