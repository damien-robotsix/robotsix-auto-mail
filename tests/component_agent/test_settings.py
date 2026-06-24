"""Unit tests for ``robotsix_auto_mail.component_agent.settings``."""

from __future__ import annotations

import pytest

from robotsix_auto_mail.component_agent.settings import ComponentAgentSettings
from robotsix_auto_mail.config import MailConfig


def _make_config(**overrides: object) -> MailConfig:
    kwargs: dict[str, object] = dict(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
    )
    kwargs.update(overrides)
    return MailConfig(**kwargs)  # type: ignore[arg-type]


class TestFromConfig:
    def test_builds_from_config(self) -> None:
        cfg = _make_config(
            component_agent_enabled=True,
            component_agent_id="my-agent",
            component_agent_broker_host="broker.example.com",
            component_agent_broker_port=8443,
            component_agent_broker_token="tok",
            component_agent_broker_tls_ca="/path/to/ca.pem",
        )
        s = ComponentAgentSettings.from_config(cfg)
        assert s.agent_id == "my-agent"
        assert s.broker_host == "broker.example.com"
        assert s.broker_port == 8443
        assert s.broker_token == "tok"
        assert s.broker_tls_ca == "/path/to/ca.pem"
        assert s.enabled is True

    def test_defaults_when_not_set(self) -> None:
        cfg = _make_config()
        s = ComponentAgentSettings.from_config(cfg)
        assert s.agent_id == "board-manager-robotsix-auto-mail"
        assert s.broker_host == ""
        assert s.broker_port == 443
        assert s.broker_token == ""
        assert s.broker_tls_ca == ""
        assert s.enabled is False

    def test_invariant_enabled_without_token_raises(self) -> None:
        # Test the ComponentAgentSettings invariant directly, since
        # MailConfig.__post_init__ now catches this first.
        with pytest.raises(ValueError, match="broker_token"):
            ComponentAgentSettings(
                agent_id="test",
                broker_host="broker.example.com",
                broker_port=443,
                broker_token="",
                broker_tls_ca="",
                enabled=True,
            )

    def test_invariant_enabled_without_host_raises(self) -> None:
        with pytest.raises(ValueError, match="broker_host"):
            ComponentAgentSettings(
                agent_id="test",
                broker_host="",
                broker_port=443,
                broker_token="tok",
                broker_tls_ca="",
                enabled=True,
            )

    def test_disabled_without_token_is_ok(self) -> None:
        s = ComponentAgentSettings(
            agent_id="test",
            broker_host="",
            broker_port=443,
            broker_token="",
            broker_tls_ca="",
            enabled=False,
        )
        assert s.enabled is False
        assert s.broker_token == ""
