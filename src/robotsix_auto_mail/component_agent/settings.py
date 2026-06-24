"""Frozen component-agent settings built from a ``MailConfig``."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from robotsix_auto_mail.config.model import MailConfig


@dataclasses.dataclass(frozen=True)
class ComponentAgentSettings:
    """Frozen view of the component-agent broker connection fields.

    Built from ``MailConfig`` via ``from_config()``.  Re-asserts the
    token-required-when-enabled invariant as defense in depth.
    """

    agent_id: str
    broker_host: str
    broker_port: int
    broker_token: str
    broker_tls_ca: str
    enabled: bool

    def __post_init__(self) -> None:
        if self.enabled:
            if not self.broker_token:
                raise ValueError("broker_token is required when enabled is True")
            if not self.broker_host:
                raise ValueError("broker_host is required when enabled is True")

    @classmethod
    def from_config(cls, config: MailConfig) -> ComponentAgentSettings:
        """Build settings from the repo-native flat ``MailConfig`` fields."""
        return cls(
            agent_id=config.component_agent_id,
            broker_host=config.component_agent_broker_host,
            broker_port=config.component_agent_broker_port,
            broker_token=config.component_agent_broker_token,
            broker_tls_ca=config.component_agent_broker_tls_ca,
            enabled=config.component_agent_enabled,
        )
