"""Unit tests for _render_attachments."""

from __future__ import annotations

from robotsix_auto_mail.server.views.detail import _render_attachments


class TestRenderAttachments:
    def test_empty_list(self):
        result = _render_attachments([])
        assert result == "<em>(none)</em>"

    def test_dict_items_without_size(self):
        result = _render_attachments(
            [{"filename": "report.pdf"}, {"filename": "image.png"}]
        )
        assert "report.pdf" in result
        assert "image.png" in result

    def test_dict_items_with_size(self):
        result = _render_attachments([{"filename": "big.zip", "size": 1_048_576}])
        assert "big.zip" in result
        assert "1,048,576 bytes" in result

    def test_dict_items_with_size_zero(self):
        result = _render_attachments([{"filename": "empty.txt", "size": 0}])
        assert "empty.txt" in result
        assert "0 bytes" in result

    def test_non_dict_items(self):
        result = _render_attachments(["just_a_string.pdf"])
        assert "just_a_string.pdf" in result

    def test_mixed_items(self):
        result = _render_attachments(
            [
                {"filename": "a.pdf", "size": 100},
                "b.txt",
                {"filename": "c.doc"},
            ]
        )
        assert "a.pdf" in result
        assert "100 bytes" in result
        assert "b.txt" in result
        assert "c.doc" in result
