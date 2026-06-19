"""Inter-agent calendar integration package.

Re-exports the public symbols used by both the detail view and the
POST handler so callers can import from ``robotsix_auto_mail.calendar``.
"""

from __future__ import annotations

from .dispatch import CalendarDispatchError as CalendarDispatchError
from .dispatch import dispatch_calendar_request as dispatch_calendar_request
from .schema import DATE_TIME_RE as DATE_TIME_RE
from .schema import CalendarEventRequest as CalendarEventRequest
from .schema import CalendarEventResponse as CalendarEventResponse
from .schema import extract_calendar_summary as extract_calendar_summary
from .schema import extract_dates_from_body as extract_dates_from_body
from .transport import build_calendar_transport as build_calendar_transport
from .transport import (
    build_calendar_transport_from_config as build_calendar_transport_from_config,
)
from .transport import build_ssl_context as build_ssl_context

__all__ = [
    "DATE_TIME_RE",
    "CalendarDispatchError",
    "CalendarEventRequest",
    "CalendarEventResponse",
    "build_calendar_transport",
    "build_calendar_transport_from_config",
    "build_ssl_context",
    "dispatch_calendar_request",
    "extract_calendar_summary",
    "extract_dates_from_body",
]
