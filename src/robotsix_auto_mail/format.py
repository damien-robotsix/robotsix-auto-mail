"""(Internal) Shared formatting helpers."""

from __future__ import annotations

from datetime import datetime

_BODY_PREVIEW_LIMIT = 150


def _format_date(raw: str) -> str:
    """Parse an ISO-8601 *raw* date and return a human-friendly string.

    Returns *raw* unchanged when parsing fails.
    """
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return raw
