"""Unit tests for the HTML sanitizer."""

from __future__ import annotations

import pytest

from robotsix_auto_mail.core._sanitize import sanitize_html


class TestSanitizeHtml:
    def test_empty_input(self) -> None:
        assert sanitize_html("") == ""
        assert sanitize_html("   ") == ""

    def test_preserves_safe_tags(self) -> None:
        result = sanitize_html("<p>Hello <b>world</b></p>")
        assert "<p>Hello <b>world</b></p>" in result

    def test_strips_script_tags(self) -> None:
        result = sanitize_html('<p>Safe</p><script>alert("xss")</script><p>More</p>')
        assert "<p>Safe</p>" in result
        assert "<p>More</p>" in result
        assert "<script>" not in result
        assert "alert" not in result

    def test_strips_style_tags(self) -> None:
        result = sanitize_html("<p>Text</p><style>body { color: red; }</style>")
        assert "<p>Text</p>" in result
        assert "<style>" not in result
        assert "color" not in result

    def test_strips_iframe(self) -> None:
        result = sanitize_html('<iframe src="http://evil.com"></iframe>')
        assert "iframe" not in result

    def test_strips_event_handlers(self) -> None:
        result = sanitize_html('<p onclick="alert(1)">Click</p>')
        assert "<p>" in result
        assert "onclick" not in result

    def test_keeps_safe_href(self) -> None:
        result = sanitize_html('<a href="https://example.com">Link</a>')
        assert 'href="https://example.com"' in result

    def test_strips_javascript_href(self) -> None:
        result = sanitize_html('<a href="javascript:alert(1)">Link</a>')
        assert "javascript:" not in result

    def test_strips_data_url_href(self) -> None:
        result = sanitize_html(
            '<a href="data:text/html,<script>alert(1)</script>">X</a>'
        )
        assert "data:" not in result.lower()

    def test_keeps_mailto_href(self) -> None:
        result = sanitize_html('<a href="mailto:user@example.com">Email</a>')
        assert 'href="mailto:user@example.com"' in result

    def test_strips_img_src(self) -> None:
        result = sanitize_html(
            '<img src="http://tracker.example.com/pixel.png" alt="pic">'
        )
        assert "src=" not in result
        assert 'alt="pic"' in result

    def test_strips_unknown_tags(self) -> None:
        result = sanitize_html("<custom-tag>Content</custom-tag>")
        assert "<custom-tag>" not in result
        assert "Content" in result

    def test_preserves_text_content(self) -> None:
        result = sanitize_html("<p>Hello &amp; welcome</p>")
        assert "Hello &amp; welcome" in result

    def test_nested_skip_tags(self) -> None:
        result = sanitize_html(
            '<div><script>var x = "<b>bold</b>";</script><p>safe</p></div>'
        )
        assert "<b>" not in result
        assert "<p>safe</p>" in result

    def test_strips_style_with_nested_elements(self) -> None:
        result = sanitize_html("<style>p { color: red; }</style><p>visible</p>")
        assert "<style>" not in result
        assert "<p>visible</p>" in result

    def test_table_structure_preserved(self) -> None:
        result = sanitize_html(
            "<table><thead><tr><th>H</th></tr></thead><tbody><tr><td>D</td></tr></tbody></table>"
        )
        assert "<table>" in result
        assert "<th>" in result
        assert "<td>" in result

    def test_blockquote_preserved(self) -> None:
        result = sanitize_html("<blockquote><p>Quote</p></blockquote>")
        assert "<blockquote>" in result
        assert "<p>Quote</p>" in result

    def test_strips_noscript(self) -> None:
        result = sanitize_html("<noscript>JS required</noscript><p>fallback</p>")
        assert "<noscript>" not in result
        assert "JS required" not in result
        assert "<p>fallback</p>" in result

    def test_strips_object_embed_applet(self) -> None:
        for tag in ("object", "embed", "applet"):
            result = sanitize_html(f"<{tag}>bad</{tag}><p>ok</p>")
            assert f"<{tag}>" not in result
            assert "<p>ok</p>" in result

    def test_keeps_safe_attributes_on_allowed_tags(self) -> None:
        result = sanitize_html('<td colspan="2" rowspan="1">Cell</td>')
        assert 'colspan="2"' in result
        assert 'rowspan="1"' in result

    def test_strips_unknown_attributes(self) -> None:
        result = sanitize_html('<p data-foo="bar" class="x">Text</p>')
        assert "data-foo" not in result
        assert "class=" not in result
        assert "<p>" in result

    @pytest.mark.parametrize(
        ("input_html", "expected_text"),
        [
            ("<p>Hello</p>", "Hello"),
            ("<p>Hello <b>world</b></p>", "<p>Hello <b>world</b></p>"),
            ("<div><p>Nested</p></div>", "Nested"),
        ],
    )
    def test_text_extraction(self, input_html: str, expected_text: str) -> None:
        result = sanitize_html(input_html)
        assert expected_text in result
