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

    def test_html_body_rendered_as_primary(self):
        record = _make_record(
            body_plain="",
            body_html="<p>Hello</p>",
        )
        body_html, body_note = _render_body(record)
        assert '<div class="email-body">' in body_html
        assert "<p>Hello</p>" in body_html
        assert "Rendered from HTML (sanitised)" in body_note

    def test_html_body_with_plain_stub(self):
        record = _make_record(
            body_plain="Click to view",
            body_html="<p>Real content</p>",
        )
        body_html, body_note = _render_body(record)
        # HTML should be the primary render even when plain text exists.
        assert '<div class="email-body">' in body_html
        assert "<p>Real content</p>" in body_html
        assert "Rendered from HTML (sanitised)" in body_note

    def test_plain_text_only_body(self):
        record = _make_record(
            body_plain="Hello world",
            body_html="",
        )
        body_html, body_note = _render_body(record)
        assert "<pre>Hello world</pre>" in body_html
        assert "email-body" not in body_html
        assert body_note == ""

    def test_html_body_sanitised(self):
        record = _make_record(
            body_plain="",
            body_html='<p>Safe</p><script>alert("xss")</script><p>More</p>',
        )
        body_html, _body_note = _render_body(record)
        assert "<p>Safe</p>" in body_html
        assert "<p>More</p>" in body_html
        assert "<script>" not in body_html
        assert "alert" not in body_html
        assert '<div class="email-body">' in body_html

    def test_html_body_remote_images_blocked(self):
        record = _make_record(
            body_plain="",
            body_html='<img src="http://tracker.example.com/pixel.png" alt="pic">',
        )
        body_html, _body_note = _render_body(record)
        assert "alt=" in body_html
        assert "src=" not in body_html
        assert "tracker" not in body_html
