"""(Internal) Shared formatting helpers."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from robotsix_llmio.core import html_to_text

if TYPE_CHECKING:
    from robotsix_auto_mail.db import MailRecord

_BODY_PREVIEW_LIMIT = 150  # lgtm[py/unused-global-variable]


def _effective_body_plain(record: MailRecord) -> str:
    """Return ``body_plain`` if it has content; otherwise strip ``body_html``.

    HTML-only emails (common in marketing/newsletters) send
    ``multipart/alternative`` with an empty ``text/plain`` part.
    This lets display code paths fall back to a stripped version of
    the HTML body instead of showing ``(no body)``.
    """
    if record.body_plain.strip():
        return record.body_plain
    text: str = html_to_text(record.body_html)
    return text


def _format_date(raw: str) -> str:
    """Parse an ISO-8601 *raw* date and return a human-friendly string.

    Returns *raw* unchanged when parsing fails.
    """
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError, TypeError:
        return raw
