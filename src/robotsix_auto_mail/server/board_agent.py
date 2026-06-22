"""Board-agent lifecycle: start and stop the board agent as a background
service.

The board agent is an optional agent-comm bridge that exposes the mill
board's full ticket lifecycle over agent-comm messages, so other agents
can drive the board programmatically.  It is off by default and configured
per deployment.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from robotsix_auto_mail.config import MailConfig

# Opaque handle returned by start_board_agent and consumed by stop_board_agent.
_BoardAgentHandle = tuple[threading.Thread, threading.Event]


def start_board_agent(config: MailConfig) -> object | None:
    """Start the board agent in a background daemon thread.

    When the ``robotsix-board-agent`` dependency is not installed the
    import is caught and the server continues without the agent.

    Returns an opaque handle (``(thread, stop_event)``) for
    :func:`stop_board_agent`, or ``None`` when the import fails.
    """
    try:
        from robotsix_agent_comm import Registry
        from robotsix_board_agent import BoardAgent, BoardAgentSettings
    except ImportError:
        print(
            "board_agent: robotsix-board-agent is not installed. "
            "The board agent will not be started. "
            "Install it with `uv sync` or `pip install robotsix-board-agent`.",
            file=sys.stderr,
        )
        return None

    registry = Registry()
    settings = BoardAgentSettings(
        board_api_url=config.board_agent_api_url,
        board_api_token=config.board_agent_api_token,
        board_repo_id=config.board_agent_repo_id,
        enable_write_ops=config.board_agent_write_ops,
    )
    agent = BoardAgent(settings=settings, registry=registry)

    stop_event = threading.Event()

    def _run_agent() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(agent.start())
            # Block until signalled to stop.
            while not stop_event.is_set():
                stop_event.wait(timeout=1.0)
            loop.run_until_complete(agent.stop())
        finally:
            loop.close()

    thread = threading.Thread(target=_run_agent, daemon=True)
    thread.start()
    return (thread, stop_event)


def stop_board_agent(handle: object | None) -> None:
    """Signal the board agent to stop and join its thread.

    No-op when *handle* is ``None``.  Never raises — exceptions from the
    cleanup are caught and logged.
    """
    if handle is None:
        return
    try:
        thread, stop_event = cast(_BoardAgentHandle, handle)
        stop_event.set()
        thread.join(timeout=5.0)
    except Exception:
        # Best-effort shutdown; never let a cleanup error crash the server.
        pass
