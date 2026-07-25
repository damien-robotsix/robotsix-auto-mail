"""Shared helper for detail view tests."""

from __future__ import annotations

from robotsix_auto_mail.db import MailRecord


def _make_record(**overrides) -> MailRecord:
    """Create a minimal MailRecord with sensible defaults for detail view tests."""
    defaults = {
        "message_id": "<test@example.com>",
        "sender": "sender@example.com",
        "subject": "Test Subject",
        "date": "2025-01-15T10:30:00",
        "body_plain": "Hello world",
        "body_html": "",
    }
    defaults.update(overrides)
    return MailRecord(**defaults)
