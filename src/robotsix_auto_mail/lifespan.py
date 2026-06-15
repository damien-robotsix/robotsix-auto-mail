"""Optional board-agent lifecycle management.

When ``board_agent_enabled`` is True, this module starts a ``BoardAgent``
that registers with the shared agent-comm ``Registry`` and drives the mill
board.  Imports are guarded because ``robotsix-board-agent`` is an optional
git dependency that may not be installed in every environment.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class BoardAgentSettings:
    """Settings for the board agent, extracted from ``MailConfig``."""

    api_url: str
    api_token: str
    repo_id: str
    enable_write_ops: bool


def start_board_agent(
    config: Any,
    *,
    agent_id: str = "board",
) -> Any | None:
    """Start the board agent if ``board_agent_enabled`` is True.

    Returns the agent instance (for later shutdown) or ``None`` if the
    agent is disabled, the ``robotsix-board-agent`` package is not
    installed, or the required config fields are missing.
    """
    if not config.board_agent_enabled:
        return None

    try:
        from robotsix_agent_comm import Registry
        from robotsix_board_agent import BoardAgent
    except ImportError:
        logger.warning(
            "board_agent_enabled=True but robotsix-board-agent is not installed"
        )
        return None

    settings = BoardAgentSettings(
        api_url=config.board_agent_api_url,
        api_token=config.board_agent_api_token,
        repo_id=config.board_agent_repo_id,
        enable_write_ops=config.board_agent_write_ops,
    )

    if not settings.api_url:
        logger.warning(
            "board_agent_enabled=True but board_agent_api_url is empty"
        )
        return None

    registry = Registry()
    agent = BoardAgent(settings, registry, agent_id=agent_id)
    agent.start()
    logger.info(
        "Board agent started (agent_id=%s, repo_id=%s)",
        agent_id,
        settings.repo_id,
    )
    return agent


def stop_board_agent(agent: Any | None) -> None:
    """Stop the board agent if it was started."""
    if agent is not None:
        try:
            agent.stop()
            logger.info("Board agent stopped")
        except Exception:
            logger.exception("Error stopping board agent")
