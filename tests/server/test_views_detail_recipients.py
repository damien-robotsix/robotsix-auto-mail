"""Unit tests for _render_recipients."""

from __future__ import annotations

from robotsix_auto_mail.server.views.detail import _render_recipients


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
