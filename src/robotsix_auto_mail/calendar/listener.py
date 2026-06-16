"""Background listener for calendar-agent response notifications.

Listens on the ``robotsix_agent_comm`` message bus for incoming
``CalendarEventResponse`` messages from the ``"robotsix-calendar"``
agent, correlates them to the originating ``MailRecord`` via
``correlation_id``, and writes the ``calendar_event_ref`` into the
local SQLite database.

Architecture (same pattern as ``board_agent.py``):
- A daemon thread runs an asyncio event loop.
- An ``Agent("robotsix-auto-mail", ...)`` instance registers an
  ``on_notification`` callback that fires for every incoming message.
- The callback validates the payload, looks up the record, and
  updates the DB.
"""

from __future__ import annotations

import asyncio
import logging
import threading

logger = logging.getLogger(__name__)

Handle = object  # opaque handle returned by start_calendar_listener


def start_calendar_listener(db_path: str) -> Handle | None:
    """Start a daemon thread running the calendar-response event loop.

    Returns an opaque handle for later ``stop_calendar_listener``, or
    ``None`` when the ``robotsix_agent_comm`` dependency is not installed.
    """
    try:
        from robotsix_agent_comm.sdk import Agent
        from robotsix_agent_comm.transport import Registry
    except ImportError:
        logger.warning(
            "robotsix_agent_comm not installed; calendar listener disabled."
        )
        return None

    registry = Registry()
    agent = Agent("robotsix-auto-mail", registry=registry)

    # Inject the db_path into the callback closure.
    def _on_notification(notification: dict) -> None:
        _handle_calendar_response(db_path, notification)

    agent.on_notification = _on_notification

    def _run_loop() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            loop.close()

    thread = threading.Thread(target=_run_loop, daemon=True, name="calendar-listener")
    thread.start()
    logger.info("Calendar listener started for db_path=%s", db_path)
    return (thread, agent)


def stop_calendar_listener(handle: Handle | None) -> None:
    """Stop the calendar listener daemon thread.

    No-op when *handle* is ``None`` (listener was never started).
    """
    if handle is None:
        return
    _thread, _agent = handle  # type: ignore[misc]
    # Agent cleanup happens when the event loop stops; the thread is a
    # daemon so it will exit when the process terminates.
    logger.info("Calendar listener stopped.")


# ---------------------------------------------------------------------------
# Internal helpers (injectable for tests)
# ---------------------------------------------------------------------------


def _handle_calendar_response(db_path: str, body: dict) -> None:
    """Parse and persist a calendar-agent response notification.

    Pure function — callable directly for tests without spinning up a
    thread or an asyncio event loop.
    """
    from robotsix_auto_mail.calendar.schema import CalendarEventResponse
    from robotsix_auto_mail.db import (
        get_record_by_correlation_id,
        init_db,
        update_calendar_event_ref,
    )

    try:
        response = CalendarEventResponse.model_validate(body)
    except Exception:
        logger.debug("Ignoring non-CalendarEventResponse notification: %s", body)
        return

    if response.status == "success":
        event_ref = response.event_ref or "success"
    else:
        # Always include the "error: " prefix so downstream rendering
        # (e.g. _render_calendar_feedback) can distinguish success from
        # failure even when the agent provides no message.
        event_ref = "error: " + (response.message or "Unknown error")

    conn = init_db(db_path, skip_migrations=True)
    try:
        record = get_record_by_correlation_id(conn, response.correlation_id)
        if record is None:
            logger.debug(
                "No MailRecord found for correlation_id=%s — "
                "response arrived before record was persisted?",
                response.correlation_id,
            )
            return
        update_calendar_event_ref(conn, record.message_id, event_ref)
        logger.info(
            "Calendar event ref updated: message_id=%s event_ref=%s",
            record.message_id,
            event_ref,
        )
    finally:
        conn.close()
