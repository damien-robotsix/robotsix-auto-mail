"""Component-agent responder for the robotsix_agent_comm broker.

Registers under agent-id ``board-manager-robotsix-auto-mail`` and serves
three typed request kinds — ``monitor`` (live telemetry), ``config-get``
(redacted snapshot), ``config-set`` (validate-then-apply with audit).

All ``robotsix_agent_comm`` imports are lazy and guarded so the server
remains importable without the optional dependency.
"""

from __future__ import annotations

from robotsix_auto_mail.component_agent.config_contract import (
    SETTABLE_KEYS as SETTABLE_KEYS,
)
from robotsix_auto_mail.component_agent.config_contract import (
    ConfigContractError as ConfigContractError,
)
from robotsix_auto_mail.component_agent.config_contract import (
    apply_config_update as apply_config_update,
)
from robotsix_auto_mail.component_agent.config_contract import (
    describe_config as describe_config,
)
from robotsix_auto_mail.component_agent.config_contract import (
    get_config_snapshot as get_config_snapshot,
)
from robotsix_auto_mail.component_agent.config_contract import (
    validate_config_update as validate_config_update,
)
from robotsix_auto_mail.component_agent.responder import (
    ComponentAgentResponder as ComponentAgentResponder,
)
from robotsix_auto_mail.component_agent.responder import (
    start_component_responder as start_component_responder,
)
from robotsix_auto_mail.component_agent.responder import (
    stop_component_responder as stop_component_responder,
)
from robotsix_auto_mail.component_agent.settings import (
    ComponentAgentSettings as ComponentAgentSettings,
)

__all__ = [
    "SETTABLE_KEYS",
    "ComponentAgentResponder",
    "ComponentAgentSettings",
    "ConfigContractError",
    "apply_config_update",
    "describe_config",
    "get_config_snapshot",
    "start_component_responder",
    "stop_component_responder",
    "validate_config_update",
]
