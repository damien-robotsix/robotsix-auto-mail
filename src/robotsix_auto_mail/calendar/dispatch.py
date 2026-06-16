"""Fire-and-forget dispatch of calendar event requests.

Sends a ``CalendarEventRequest`` to the ``"robotsix-calendar"`` agent
over the ``robotsix_agent_comm`` message bus.  All agent-comm imports
are lazy so the server remains functional when the optional dependency
is not installed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schema import CalendarEventRequest

logger = logging.getLogger(__name__)

# Module-level singleton ``Registry`` shared across the process lifetime.
# Created lazily on first call to :func:`dispatch_calendar_request`.
_registry: object | None = None


class CalendarDispatchError(Exception):
    """Raised when a calendar request cannot be delivered."""


def _get_registry() -> object:
    """Return the module-level ``Registry`` singleton, creating it on demand."""
    global _registry
    if _registry is None:
        from robotsix_agent_comm.transport import Registry

        _registry = Registry()
    return _registry


def dispatch_calendar_request(event: CalendarEventRequest) -> None:
    """Send *event* to the ``"robotsix-calendar"`` agent.

    Uses ``Agent.send_notification`` (fire-and-forget) — no reply is
    awaited.  Ticket 3 adds the response listener.

    Raises:
        CalendarDispatchError: When the agent-comm stack is unavailable,
            the calendar agent is not registered, or delivery fails.
    """
    try:
        from robotsix_agent_comm.sdk import Agent
        from robotsix_agent_comm.transport import AgentNotFoundError, DeliveryError
    except ImportError as exc:
        raise CalendarDispatchError("Agent communication is not available") from exc

    registry = _get_registry()

    try:
        agent = Agent(registry=registry)
        agent.send_notification(
            recipient="robotsix-calendar",
            body=event.model_dump(),
        )
    except AgentNotFoundError as exc:
        raise CalendarDispatchError("Calendar agent is not available") from exc
    except DeliveryError as exc:
        raise CalendarDispatchError(
            f"Failed to deliver calendar request: {exc}"
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected error dispatching calendar request")
        raise CalendarDispatchError(
            f"Failed to deliver calendar request: {exc}"
        ) from exc
