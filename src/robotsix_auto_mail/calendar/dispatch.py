"""Request/response dispatch of calendar event requests.

Sends an ``add_to_calendar`` request to the ``"robotsix-calendar"`` agent over
the ``robotsix_agent_comm`` broker and returns the agent's correlated reply.
All agent-comm imports are lazy so the server remains functional when the
optional dependency is not installed.

When the ``calendar_transport`` config field is ``"brokered"``, the transport
factory builds a ``BrokeredRegistry`` + ``NetworkedBrokerTransport`` that connect
to the secured broker (TLS + token auth) and the agent runs in mailbox/pull mode.
Broker connection, authentication, delivery, and calendar-side errors are all
mapped to ``CalendarDispatchError`` with actionable messages.
"""

from __future__ import annotations

import contextlib
import logging
import ssl
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from robotsix_auto_mail.config.model import MailConfig

    from .schema import CalendarEventRequest

logger = logging.getLogger(__name__)

# Agent ids on the broker. auto-mail sends as ``robotsix-auto-mail`` (its
# provisioned token's principal) and addresses the calendar agent by its id.
_SELF_AGENT_ID = "robotsix-auto-mail"
_CALENDAR_AGENT_ID = "robotsix-calendar"

# How long to wait for the calendar agent to create the event and reply. The
# agent may make a CalDAV round-trip (and an LLM call to resolve dates), so this
# is generous.
_REQUEST_TIMEOUT = 60.0

# Calendar dispatch is a low-frequency, user-driven action. Serialise it so
# concurrent requests never have two transient ``robotsix-auto-mail`` agents
# polling the same broker mailbox at once (which would cross-deliver their
# correlated replies and time each other out).
_dispatch_lock = threading.Lock()


class CalendarDispatchError(Exception):
    """Raised when a calendar request cannot be delivered or is rejected."""


def dispatch_calendar_request(
    event: CalendarEventRequest,
    *,
    config: MailConfig | None = None,
) -> str:
    """Send *event* to the ``"robotsix-calendar"`` agent and return its result.

    Issues an agent-comm **request** carrying ``{"add_to_calendar": ...}`` and
    waits for the correlated reply. When *config* selects the ``"brokered"``
    transport the agent connects to the secured broker in mailbox/pull mode;
    otherwise the in-process registry is used.

    Args:
        event: The calendar event request to dispatch.
        config: Optional ``MailConfig`` for transport selection.

    Returns:
        A human-readable reference for the created event (e.g. a confirmation
        line or the event UID).

    Raises:
        CalendarDispatchError: When the agent-comm stack is unavailable, the
            broker is unreachable, authentication fails, delivery fails, no
            reply arrives, or the calendar agent reports an error.
    """
    try:
        from robotsix_agent_comm.protocol import Error
        from robotsix_agent_comm.sdk import Agent
        from robotsix_agent_comm.transport import (
            AgentNotFoundError,
            DeliveryError,
            TransportError,
            TransportTimeoutError,
        )
    except ImportError as exc:
        raise CalendarDispatchError("Agent communication is not available") from exc

    # Build the transport pair from config when available, else in-process.
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

    agent_kwargs: dict[str, Any] = {"registry": registry}
    if transport_obj is not None:
        agent_kwargs["transport"] = transport_obj
        agent_kwargs["pull"] = True
    agent = Agent(_SELF_AGENT_ID, **agent_kwargs)

    with _dispatch_lock:
        agent.start()
        try:
            reply = agent.send_request(
                _CALENDAR_AGENT_ID,
                {"add_to_calendar": event.model_dump()},
                timeout=_REQUEST_TIMEOUT,
            )
        except AgentNotFoundError as exc:
            raise CalendarDispatchError("Calendar agent is not available") from exc
        except (DeliveryError, TransportTimeoutError, TransportError) as exc:
            raise CalendarDispatchError(
                f"Failed to deliver calendar request: {exc}"
            ) from exc
        except Exception as exc:
            logger.exception("Unexpected error dispatching calendar request")
            raise CalendarDispatchError(
                f"Failed to deliver calendar request: {exc}"
            ) from exc
        finally:
            with contextlib.suppress(Exception):
                agent.stop()

    return _interpret_reply(reply, Error)


def _interpret_reply(reply: Any, error_cls: type) -> str:
    """Map the calendar agent's reply to a success reference, or raise.

    The calendar agent replies with ``{"result": {...}}`` on success or
    ``{"error": {...}}`` on a calendar-side failure; an agent-comm-level
    failure arrives as an ``Error`` message.
    """
    if isinstance(reply, error_cls):
        message = _reply_error_message(getattr(reply, "body", None))
        raise CalendarDispatchError(f"Calendar agent error: {message}")

    body = getattr(reply, "body", None)
    if isinstance(body, dict):
        if isinstance(body.get("error"), dict):
            message = body["error"].get("message") or "unknown error"
            raise CalendarDispatchError(f"Calendar agent error: {message}")
        result = body.get("result")
        if isinstance(result, dict):
            event = result.get("event")
            uid = event.get("uid") if isinstance(event, dict) else None
            return str(result.get("confirmation_text") or uid or "Event created")

    raise CalendarDispatchError("Calendar agent returned a malformed response")


def _reply_error_message(body: Any) -> str:
    """Best-effort extraction of a message from an agent-comm Error body."""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if body.get("message"):
            return str(body["message"])
    return "unknown error"
