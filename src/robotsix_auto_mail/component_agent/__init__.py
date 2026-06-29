"""Component-agent HTTP API for the board server.

Serves three endpoints — monitor (live telemetry), config-get (redacted
snapshot), config-set (validate-then-apply with audit) — directly over
HTTP without the agent-comm broker.
"""
