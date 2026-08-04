"""Unit tests for HTML sanitizer (_sanitize.py)."""

from __future__ import annotations

import pytest

from robotsix_auto_mail.core._sanitize import sanitize_html


class TestSanitizeHtml:
    def test_passes_safe_html(self):
        result = sanitize_html("<p>Hello <b>World</b></p>")
        assert "<p>Hello <b>World</b></p>" in result

    def test_strips_script_tags_and_content(self):
        result = sanitize_html("<div><script>alert('xss')</script><p>Safe</p></div>")
        assert "script" not in result
        assert "alert" not in result
        assert "<p>Safe</p>" in result

    def test_strips_style_tags_and_content(self):
        result = sanitize_html(
            "<div><style>body { color: red; }</style><p>Safe</p></div>"
        )
        assert "style" not in result
        assert "color" not in result
        assert "<p>Safe</p>" in result

    def test_strips_event_handlers(self):
        result = sanitize_html('<div onclick="bad()" onload="bad()">Safe</div>')
        assert "onclick" not in result
        assert "onload" not in result
        assert "Safe" in result

    def test_strips_img_src(self):
        result = sanitize_html('<img src="https://track.example/pixel.png" alt="pic">')
        assert "src" not in result
        assert 'alt="pic"' in result

    def test_preserves_img_alt_width_height(self):
        result = sanitize_html('<img src="x.png" alt="Logo" width="100" height="50">')
        assert "src" not in result
        assert 'alt="Logo"' in result
        assert 'width="100"' in result
        assert 'height="50"' in result

    def test_strips_iframe(self):
        result = sanitize_html(
            '<iframe src="https://evil.example"></iframe><p>Safe</p>'
        )
        assert "iframe" not in result
        assert "evil" not in result

    def test_escapes_text_content(self):
        result = sanitize_html("<p>a < b & c</p>")
        assert "&lt;" in result
        assert "&amp;" in result

    def test_preserves_links(self):
        result = sanitize_html('<a href="https://example.com" title="Go">Click</a>')
        assert 'href="https://example.com"' in result
        assert 'title="Go"' in result

    def test_strips_link_onclick(self):
        result = sanitize_html(
            '<a href="https://safe.example" onclick="steal()">Click</a>'
        )
        assert "onclick" not in result
        assert 'href="https://safe.example"' in result

    def test_preserves_table_structure(self):
        result = sanitize_html("<table><tr><td>A</td><td>B</td></tr></table>")
        assert "<table>" in result
        assert "<tr>" in result
        assert "<td>A</td>" in result

    def test_strips_form_elements(self):
        """Form and input tags are stripped; content after void elements
        (like input) is preserved."""
        result = sanitize_html(
            '<form action="/evil"><input name="x">Safe</form><p>After</p>'
        )
        assert "form" not in result
        assert "input" not in result
        assert "Safe" in result
        assert "<p>After</p>" in result

    def test_empty_string(self):
        assert sanitize_html("") == ""

    def test_plain_text_passthrough(self):
        result = sanitize_html("Just some text without markup")
        assert result == "Just some text without markup"

    def test_nested_removed_tags(self):
        result = sanitize_html(
            "<div><script>var x = '<b>nested</b>';</script><p>Safe</p></div>"
        )
        assert "script" not in result
        assert "nested" not in result
        assert "<p>Safe</p>" in result

    def test_unknown_tags_stripped(self):
        result = sanitize_html("<custom-tag>Content</custom-tag><p>Safe</p>")
        assert "custom-tag" not in result
        assert "<p>Safe</p>" in result

    def test_handles_malformed_html(self):
        """Malformed HTML should still produce safe output."""
        result = sanitize_html("<p>Unclosed<script>alert(1)")
        assert "script" not in result
        assert "alert(1)" not in result

    def test_keeps_safe_global_attrs(self):
        result = sanitize_html('<div id="main" class="content" title="Tip">X</div>')
        assert 'id="main"' in result
        assert 'class="content"' in result
        assert 'title="Tip"' in result

    @pytest.mark.parametrize(
        "tag",
        ["script", "style", "iframe", "object", "applet"],
    )
    def test_removed_tags_strip_content(self, tag):
        """Non-void removed tags strip their content."""
        result = sanitize_html(f"<{tag}>bad</{tag}><p>Safe</p>")
        assert "bad" not in result
        assert tag not in result
        assert "<p>Safe</p>" in result

    @pytest.mark.parametrize(
        "tag",
        ["embed", "input", "link", "meta", "base", "source"],
    )
    def test_removed_void_tags(self, tag):
        """Void removed tags are stripped; text after them is preserved."""
        result = sanitize_html(f"<{tag} attrs>text after<p>Safe</p>")
        assert tag not in result
        assert "text after" in result
        assert "<p>Safe</p>" in result

    @pytest.mark.parametrize(
        "tag",
        ["form", "select", "textarea", "button"],
    )
    def test_unwrapped_tags_preserve_content(self, tag):
        """Unwrapped tags strip the tag but keep child content."""
        result = sanitize_html(f"<{tag}>keep me</{tag}>")
        assert tag not in result
        assert "keep me" in result

    # -- URL scheme filtering -------------------------------------------------

    @pytest.mark.parametrize(
        "href",
        [
            "javascript:alert(document.cookie)",
            "JavaScript:alert(1)",
            "JAVASCRIPT:void(0)",
            "data:text/html,<script>alert(1)</script>",
            "DATA:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "vbscript:msgbox(1)",
            "VBScript:msgbox(1)",
        ],
    )
    def test_strips_dangerous_href_schemes(self, href):
        """href values with dangerous schemes are stripped entirely."""
        result = sanitize_html(f'<a href="{href}">click</a>')
        assert "href" not in result
        assert "click" in result

    @pytest.mark.parametrize(
        "href",
        [
            "https://example.com",
            "http://example.com/path?q=1",
            "mailto:user@example.com",
            "tel:+1234567890",
            "ftp://files.example.com",
            "/relative/path",
            "../relative/path",
            "#fragment",
            "",
        ],
    )
    def test_keeps_safe_href_schemes(self, href):
        """Safe href values are preserved."""
        result = sanitize_html(f'<a href="{href}">click</a>')
        assert "href" in result
        assert "click" in result

    def test_strips_javascript_href_with_whitespace(self):
        """Whitespace before the scheme is tolerated."""
        result = sanitize_html('<a href="   javascript:alert(1)">click</a>')
        assert "href" not in result

    def test_non_url_href_not_affected(self):
        """href values without a colon (relative paths, etc.) are kept."""
        result = sanitize_html('<a href="about">about</a>')
        assert 'href="about"' in result

    def test_javascript_href_in_non_a_tag_ignored(self):
        """Only <a href> is filtered; other tags with href are harmless
        because the tag itself isn't in the allow-set."""
        result = sanitize_html('<link href="javascript:alert(1)">text')
        # <link> is a void strip tag, so it's removed entirely
        assert "javascript" not in result
