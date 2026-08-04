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

    def test_html_body_primary(self):
        """When body_html is present it is rendered as the primary body."""
        record = _make_record(
            body_plain="Plain fallback",
            body_html="<p>Hello <b>World</b></p>",
        )
        body_html, body_note = _render_body(record)
        assert '<div class="email-body">' in body_html
        assert "<p>Hello <b>World</b></p>" in body_html
        assert "Rendered from HTML body" in body_note

    def test_html_body_sanitized(self):
        """Scripts and event handlers are stripped from rendered HTML."""
        record = _make_record(
            body_plain="",
            body_html=(
                "<div onclick='alert(1)'><script>bad()</script><p>Safe</p></div>"
            ),
        )
        body_html, _body_note = _render_body(record)
        assert "script" not in body_html
        assert "onclick" not in body_html
        assert "<p>Safe</p>" in body_html

    def test_html_body_img_src_stripped(self):
        """Remote image sources are stripped to prevent tracking pixels."""
        record = _make_record(
            body_plain="",
            body_html='<img src="https://track.example/pixel.png" alt="pic">',
        )
        body_html, _body_note = _render_body(record)
        assert "src" not in body_html
        assert 'alt="pic"' in body_html

    def test_plain_text_body(self):
        """When only body_plain is present, it is shown in <pre>."""
        record = _make_record(
            body_plain="Hello world",
            body_html="",
        )
        body_html, body_note = _render_body(record)
        assert "<pre>Hello world</pre>" in body_html
        assert body_note == ""

    def test_html_only_body(self):
        """When only body_html is present, it is rendered as HTML."""
        record = _make_record(
            body_plain="",
            body_html="<p>HTML only</p>",
        )
        body_html, body_note = _render_body(record)
        assert '<div class="email-body">' in body_html
        assert "<p>HTML only</p>" in body_html
        assert "Rendered from HTML body" in body_note
