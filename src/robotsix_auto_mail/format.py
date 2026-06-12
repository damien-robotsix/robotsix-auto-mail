"""(Internal) Shared formatting helpers."""

from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from robotsix_auto_mail.db import MailRecord

_BODY_PREVIEW_LIMIT = 150


class _HTMLStripper(HTMLParser):
    """Collect text nodes from HTML, discarding tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)


def _strip_html(html_text: str) -> str:
    """Strip HTML tags using stdlib :class:`HTMLParser`.

    Returns the plain-text content with whitespace runs collapsed.
    """
    if not html_text or not html_text.strip():
        return ""
    stripper = _HTMLStripper()
    stripper.feed(html_text)
    text = "".join(stripper._parts)
    # Collapse whitespace runs.
    return " ".join(text.split())


def _effective_body_plain(record: MailRecord) -> str:
    """Return ``body_plain`` if it has content; otherwise strip ``body_html``.

    HTML-only emails (common in marketing/newsletters) send
    ``multipart/alternative`` with an empty ``text/plain`` part.
    This lets display code paths fall back to a stripped version of
    the HTML body instead of showing ``(no body)``.
    """
    if record.body_plain.strip():
        return record.body_plain
    return _strip_html(record.body_html)


def _format_date(raw: str) -> str:
    """Parse an ISO-8601 *raw* date and return a human-friendly string.

    Returns *raw* unchanged when parsing fails.
    """
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return raw
