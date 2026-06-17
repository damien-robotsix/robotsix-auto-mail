"""(Internal) Shared formatting helpers."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

try:
    from robotsix_llmio.core import html_to_text
except ImportError:
    # Fallback for robotsix_llmio builds that predate ``html_to_text``
    # landing in ``robotsix_llmio.core`` (core/text_utils.py). This mirrors
    # that helper (stdlib ``re`` + ``html`` only) so the produced plaintext
    # stays equivalent regardless of the installed robotsix_llmio version.
    import html as _html
    import re as _re

    _DROP_BLOCKS = _re.compile(
        r"<(script|style|noscript|svg)\b[^>]*>.*?</\1>",
        _re.IGNORECASE | _re.DOTALL,
    )
    _TAG = _re.compile(r"<[^>]+>")
    _WHITESPACE = _re.compile(r"\s+")

    def html_to_text(html_text: str) -> str:
        """Strip HTML markup down to whitespace-collapsed plaintext."""
        text = _DROP_BLOCKS.sub(" ", html_text)
        text = _TAG.sub(" ", text)
        text = _html.unescape(text)
        return _WHITESPACE.sub(" ", text).strip()


if TYPE_CHECKING:
    from robotsix_auto_mail.db import MailRecord

_BODY_PREVIEW_LIMIT = 150


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
