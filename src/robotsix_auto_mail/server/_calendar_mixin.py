"""Calendar-request handler mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from robotsix_auto_mail.calendar import (
    CalendarDispatchError,
    CalendarEventRequest,
    dispatch_calendar_request,
    extract_dates_from_body,
)

logger = logging.getLogger(__name__)


class _CalendarMixin:
    """Mixin providing the ``POST /add-to-calendar`` handler."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    def _handle_add_to_calendar(self) -> None:
        """Handle ``POST /add-to-calendar`` — dispatch a calendar request.

        1. Parse ``message_id`` from the URL-encoded POST body.
        2. 400 JSON if ``message_id`` is missing/empty.
        3. Look up the ``MailRecord``; 404 JSON if not found.
        4. Build a ``CalendarEventRequest``.
        5. Persist ``correlation_id`` **before** dispatch so the listener
           can correlate the response even if it arrives before dispatch
           returns.
        6. 200 JSON on success, 502 JSON on ``CalendarDispatchError``,
           500 JSON on unexpected errors (logged server-side).
        """
        from robotsix_auto_mail.db import (
            get_record_by_message_id,
            init_db,
            update_calendar_correlation_id,
        )

        # -- 1. Parse message_id --
        f = self._parse_request_body("message_id")
        message_id = f.get("message_id", "")

        if not message_id:
            self._serve_json(
                {"status": "error", "message": "Missing message_id"},
                status=400,
            )
            return

        # -- 2. Look up MailRecord --
        conn = init_db(self.db_path, skip_migrations=True)
        try:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._serve_json(
                    {"status": "error", "message": "Not found"},
                    status=404,
                )
                return

            # -- 3. Build request --
            event = CalendarEventRequest(
                message_id=record.message_id,
                subject=record.subject,
                sender=record.sender,
                body_text=_get_body_plain(record),
                email_date=record.date,
                extracted_dates=extract_dates_from_body(_get_body_plain(record)),
            )

            # -- 4. Persist correlation_id before dispatch (race fix) --
            update_calendar_correlation_id(conn, message_id, event.correlation_id)
        finally:
            conn.close()

        # -- 5. Dispatch (fire-and-forget) --
        try:
            dispatch_calendar_request(event)
        except CalendarDispatchError as exc:
            self._serve_json(
                {"status": "error", "message": str(exc)},
                status=502,
            )
            return
        except Exception:
            logger.exception("Unexpected error in /add-to-calendar")
            self._serve_json(
                {"status": "error", "message": "Internal error"},
                status=500,
            )
            return

        self._serve_json({"status": "dispatched"}, status=200)


def _get_body_plain(record: object) -> str:
    """Return the plain-text body for *record*, avoiding a circular import."""
    from robotsix_auto_mail.format import _effective_body_plain

    return _effective_body_plain(record)  # type: ignore[arg-type]
