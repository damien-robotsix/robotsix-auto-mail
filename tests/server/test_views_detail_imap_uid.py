"""Unit tests for _render_imap_uid_section."""

from __future__ import annotations

from robotsix_auto_mail.server.views.detail import _render_imap_uid_section

from ._view_helpers import _make_record


class TestRenderImapUidSection:
    def test_none_uid_returns_empty(self):
        record = _make_record(imap_uid=None)
        result = _render_imap_uid_section(record)
        assert result == ""

    def test_present_uid_shows_section(self):
        record = _make_record(imap_uid=42)
        result = _render_imap_uid_section(record)
        assert "IMAP UID" in result
        assert "42" in result
