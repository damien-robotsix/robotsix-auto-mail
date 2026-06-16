"""Calendar event request schema and extraction helpers.

Defines the inter-agent message contract shared between auto-mail and
the robotsix-calendar agent (Ticket 4).  Also hosts the date/time regex
and extraction helpers so both the detail view and the POST handler can
share a single source of truth.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from robotsix_auto_mail.db import MailRecord

# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

DATE_TIME_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}"  # ISO dates: 2025-06-15
    r"|\d{1,2}/\d{1,2}/\d{2,4}"  # US/EU dates: 6/15/2025
    r"|\d{1,2}\.\d{1,2}\.\d{2,4}"  # dotted dates: 15.06.2025
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}"  # Jun 15
    r"|\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)"  # times: 3:00 PM
    r"\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class CalendarEventRequest(BaseModel):
    """Message sent to the ``"robotsix-calendar"`` agent to request a
    calendar event creation.

    Attributes:
        correlation_id: UUID (auto-generated) for end-to-end tracking.
        message_id: The email's ``Message-ID`` header, mapping back to
            the originating ``MailRecord``.
        subject: Email subject line.
        sender: ``From`` address of the email.
        body_text: Plain-text body (the calendar agent may LLM-parse
            it for event details).
        email_date: ISO-8601 date of the email.
        extracted_dates: Date/time strings already extracted from the
            body by ``DATE_TIME_RE`` (up to 10 unique matches, in
            encounter order).
    """

    correlation_id: str = Field(default_factory=lambda: uuid4().hex)
    message_id: str
    subject: str
    sender: str
    body_text: str
    email_date: str
    extracted_dates: list[str] = Field(default_factory=list)


class CalendarEventResponse(BaseModel):
    """Message received from the ``"robotsix-calendar"`` agent in response
    to a ``CalendarEventRequest``.

    Attributes:
        correlation_id: UUID matching the originating request, used to
            correlate the response back to the ``MailRecord``.
        status: ``"success"`` or ``"error"``.
        event_ref: Human-readable event reference (e.g. a calendar link
            or event ID) on success; empty string on error.
        message: Human-readable feedback message (e.g. error reason).
    """

    correlation_id: str
    status: str
    event_ref: str = ""
    message: str = ""


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def extract_dates_from_body(body: str) -> list[str]:
    """Return up to 10 unique date/time strings found in *body*.

    Uniqueness is encounter-order (first seen wins) via ``dict.fromkeys``.
    """
    return list(dict.fromkeys(DATE_TIME_RE.findall(body)))[:10]


def extract_calendar_summary(record: MailRecord) -> str:
    """Extract a human-readable calendar summary from *record*.

    Returns a multi-line string listing the subject, email date, and any
    date/time references found in the body text (up to 10).
    """
    from robotsix_auto_mail.format import _effective_body_plain, _format_date

    lines: list[str] = []
    lines.append(f"Subject: {record.subject.strip() or '(no subject)'}")
    lines.append(f"Email date: {_format_date(record.date)}")

    body = _effective_body_plain(record)
    if body:
        matches = extract_dates_from_body(body)
        if matches:
            lines.append("Date/time references in body: " + ", ".join(matches))
    return "\n".join(lines)
