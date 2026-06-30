"""Mixin that adds component-agent HTTP routes to ``BoardHandler``.

Provides three route handlers — ``_handle_component_agent_monitor``,
``_handle_component_agent_config_get``, and
``_handle_component_agent_config_set`` — that delegate to a
``ComponentAgentResponder`` instance stored on the handler.

All three return HTTP 503 when no responder is configured.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from robotsix_auto_mail.server._component_agent_responder import (
        ComponentAgentResponder,
    )


class _ComponentAgentApiMixin:
    """Route handlers for the component-agent HTTP API.

    Requires ``self._component_responder`` (``ComponentAgentResponder |
    None``) and ``self._serve_json(payload, status)`` from ``BoardHandler``.
    """

    _component_responder: ComponentAgentResponder | None

    def _handle_component_agent_monitor(self) -> None:
        """GET /api/component-agent/monitor — live telemetry."""
        if self._component_responder is None:
            self._serve_json(  # type: ignore[attr-defined]
                {"error": "component agent not configured"}, status=503
            )
            return
        self._serve_json(  # type: ignore[attr-defined]
            self._component_responder._monitor(), status=200
        )

    def _handle_component_agent_config_get(self) -> None:
        """GET /api/component-agent/config — redacted config snapshot."""
        if self._component_responder is None:
            self._serve_json(  # type: ignore[attr-defined]
                {"error": "component agent not configured"}, status=503
            )
            return
        self._serve_json(  # type: ignore[attr-defined]
            self._component_responder._config_get(), status=200
        )

    def _handle_component_agent_config_set(self) -> None:
        """POST /api/component-agent/config — apply a config update.

        Expects a JSON body with an ``"updates"`` dict.
        """
        if self._component_responder is None:
            self._serve_json(  # type: ignore[attr-defined]
                {"error": "component agent not configured"}, status=503
            )
            return

        # Read the request body.
        content_length = int(self.headers.get("Content-Length", 0))  # type: ignore[attr-defined]
        if content_length == 0:
            self._serve_json(  # type: ignore[attr-defined]
                {
                    "error": {
                        "code": "invalid_request",
                        "message": "missing request body",
                    }
                },
                status=400,
            )
            return

        raw_body = self.rfile.read(content_length)  # type: ignore[attr-defined]
        try:
            body: Any = json.loads(raw_body)
        except json.JSONDecodeError:
            self._serve_json(  # type: ignore[attr-defined]
                {
                    "error": {
                        "code": "invalid_request",
                        "message": "invalid JSON body",
                    }
                },
                status=400,
            )
            return

        if not isinstance(body, dict):
            self._serve_json(  # type: ignore[attr-defined]
                {
                    "error": {
                        "code": "invalid_request",
                        "message": "request body must be a JSON object",
                    }
                },
                status=400,
            )
            return

        updates = body.get("updates")
        if updates is None:
            self._serve_json(  # type: ignore[attr-defined]
                {
                    "error": {
                        "code": "invalid_request",
                        "message": "missing 'updates' key in request body",
                    }
                },
                status=400,
            )
            return

        if not isinstance(updates, dict):
            self._serve_json(  # type: ignore[attr-defined]
                {
                    "error": {
                        "code": "invalid_request",
                        "message": "'updates' must be a dict",
                    }
                },
                status=400,
            )
            return

        result: dict[str, Any] = self._component_responder.config_set_direct(updates)
        status_code = 400 if "error" in result else 200
        self._serve_json(result, status=status_code)  # type: ignore[attr-defined]
