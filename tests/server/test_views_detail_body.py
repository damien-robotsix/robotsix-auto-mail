"""Unit tests for _render_body."""

from __future__ import annotations

from robotsix_auto_mail.server.views.detail import _render_body

from ._view_helpers import _make_record


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
