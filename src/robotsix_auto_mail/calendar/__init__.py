"""Inter-agent calendar integration package.

Re-exports the public symbols used by both the detail view and the
POST handler so callers can import from ``robotsix_auto_mail.calendar``.
"""

from __future__ import annotations

from .dispatch import CalendarDispatchError as CalendarDispatchError
from .dispatch import dispatch_calendar_request as dispatch_calendar_request
from .listener import start_calendar_listener as start_calendar_listener
from .listener import stop_calendar_listener as stop_calendar_listener
from .schema import DATE_TIME_RE as DATE_TIME_RE
from .schema import CalendarEventRequest as CalendarEventRequest
from .schema import CalendarEventResponse as CalendarEventResponse
from .schema import extract_calendar_summary as extract_calendar_summary
from .schema import extract_dates_from_body as extract_dates_from_body

__all__ = [
    "DATE_TIME_RE",
    "CalendarDispatchError",
    "CalendarEventRequest",
    "CalendarEventResponse",
    "dispatch_calendar_request",
    "extract_calendar_summary",
    "extract_dates_from_body",
    "start_calendar_listener",
    "stop_calendar_listener",
]
