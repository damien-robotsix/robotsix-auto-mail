"""Fire-and-forget dispatch of calendar event requests.

Sends a ``CalendarEventRequest`` to the ``"robotsix-calendar"`` agent
over the ``robotsix_agent_comm`` message bus.  All agent-comm imports
are lazy so the server remains functional when the optional dependency
is not installed.

When the ``CALENDAR_TRANSPORT`` config field is set to ``"brokered"``,
the transport factory builds a ``BrokeredRegistry`` and
``NetworkedBrokerTransport`` that connect to the secured broker server
(TLS + token auth).  Broker connection, authentication, and delivery
failures are all mapped to ``CalendarDispatchError`` with actionable
messages.
"""

from __future__ import annotations

import logging
import ssl
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from robotsix_auto_mail.config.model import MailConfig

    from .schema import CalendarEventRequest

logger = logging.getLogger(__name__)


class CalendarDispatchError(Exception):
    """Raised when a calendar request cannot be delivered."""


def dispatch_calendar_request(
    event: CalendarEventRequest,
    *,
    config: MailConfig | None = None,
) -> None:
    """Send *event* to the ``"robotsix-calendar"`` agent.

    Uses ``Agent.send_notification`` (fire-and-forget) — no reply is
    awaited.  Ticket 3 adds the response listener.

    When *config* is provided, the transport is selected according to
    ``config.calendar_transport``: ``"in-process"`` (default) uses a
    local ``Registry``; ``"brokered"`` connects to the secured broker
    server.  When *config* is ``None``, the in-process path is used.

    Args:
        event: The calendar event request to dispatch.
        config: Optional ``MailConfig`` for transport selection.

    Raises:
        CalendarDispatchError: When the agent-comm stack is unavailable,
            the calendar agent is not registered, the broker is
            unreachable, authentication fails, or delivery fails.
    """
    try:
        from robotsix_agent_comm.sdk import Agent
        from robotsix_agent_comm.transport import AgentNotFoundError, DeliveryError
    except ImportError as exc:
        raise CalendarDispatchError("Agent communication is not available") from exc

    # Build transport pair from config when available, else default to
    # in-process.
    try:
        if config is not None:
            from .transport import build_calendar_transport_from_config

            registry, transport_obj = build_calendar_transport_from_config(config)
        else:
            from .transport import _get_in_process_registry

            registry = _get_in_process_registry()
            transport_obj = None
    except (ImportError, ValueError) as exc:
        raise CalendarDispatchError(
            f"Calendar broker configuration incomplete: {exc}"
        ) from exc
    except ssl.SSLError as exc:
        raise CalendarDispatchError(
            f"Calendar broker TLS handshake failed: {exc}"
        ) from exc
    except OSError as exc:
        raise CalendarDispatchError(f"Calendar broker unreachable: {exc}") from exc

    try:
        agent_kwargs: dict[str, object] = {"registry": registry}
        if transport_obj is not None:
            agent_kwargs["transport"] = transport_obj
        # ``agent_id`` is a required positional on ``Agent.__init__``; pass the
        # sender id (matching listener.py's ``Agent("robotsix-auto-mail", …)``).
        # Omitting it raised ``TypeError: Agent.__init__() missing 1 required
        # positional argument`` whenever agent-comm was actually installed.
        agent = Agent("robotsix-auto-mail", **agent_kwargs)
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
