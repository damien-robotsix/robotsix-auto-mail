"""Component-agent responder for the robotsix_agent_comm broker.

Registers under agent-id ``board-manager-robotsix-auto-mail`` and serves
three typed request kinds — ``monitor`` (live telemetry), ``config-get``
(redacted snapshot), ``config-set`` (validate-then-apply with audit).

All ``robotsix_agent_comm`` imports are lazy and guarded so the server
remains importable without the optional dependency.
"""
