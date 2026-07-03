"""Unit tests for ``_ComponentAgentApiMixin`` methods.

Drives the mixin directly against a mock handler *self*, isolating the
logic from the HTTP transport and covering every error branch.
"""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

from robotsix_auto_mail.server._component_agent_mixin import _ComponentAgentApiMixin

# ---------------------------------------------------------------------------
# Fake handler factory
# ---------------------------------------------------------------------------


class _FakeHandler(_ComponentAgentApiMixin):
    """Concrete handler that wires the ``BoardHandlerProtocol`` attributes
    to MagicMock defaults so mixin methods can be called directly."""

    def __init__(self, *, component_responder: Any = None) -> None:
        self._component_responder = component_responder
        self.headers: dict[str, str] = {}
        self.rfile = mock.MagicMock()
        self._serve_json = mock.MagicMock()


# ---------------------------------------------------------------------------
# _handle_component_agent_monitor
# ---------------------------------------------------------------------------


class TestHandleComponentAgentMonitor:
    def test_returns_503_when_no_responder(self) -> None:
        """Returns 503 error when _component_responder is None."""
        handler = _FakeHandler(component_responder=None)
        handler._handle_component_agent_monitor()
        handler._serve_json.assert_called_once_with(
            {"error": "component agent not configured"}, status=503
        )

    def test_delegates_to_responder_monitor(self) -> None:
        """Delegates to _component_responder._monitor() and returns 200."""
        mock_responder = mock.MagicMock()
        monitor_result = {"agent_id": "test", "capabilities": {}}
        mock_responder._monitor.return_value = monitor_result
        handler = _FakeHandler(component_responder=mock_responder)
        handler._handle_component_agent_monitor()
        mock_responder._monitor.assert_called_once_with()
        handler._serve_json.assert_called_once_with(monitor_result, status=200)


# ---------------------------------------------------------------------------
# _handle_component_agent_config_get
# ---------------------------------------------------------------------------


class TestHandleComponentAgentConfigGet:
    def test_returns_503_when_no_responder(self) -> None:
        """Returns 503 error when _component_responder is None."""
        handler = _FakeHandler(component_responder=None)
        handler._handle_component_agent_config_get()
        handler._serve_json.assert_called_once_with(
            {"error": "component agent not configured"}, status=503
        )

    def test_delegates_to_responder_config_get(self) -> None:
        """Delegates to _component_responder._config_get() and returns 200."""
        mock_responder = mock.MagicMock()
        config_result: dict[str, Any] = {"config": {}, "describe": {}}
        mock_responder._config_get.return_value = config_result
        handler = _FakeHandler(component_responder=mock_responder)
        handler._handle_component_agent_config_get()
        mock_responder._config_get.assert_called_once_with()
        handler._serve_json.assert_called_once_with(config_result, status=200)


# ---------------------------------------------------------------------------
# _handle_component_agent_config_set
# ---------------------------------------------------------------------------


class TestHandleComponentAgentConfigSet:
    # -- responder=None ---------------------------------------------------

    def test_returns_503_when_no_responder(self) -> None:
        """Returns 503 error when _component_responder is None."""
        handler = _FakeHandler(component_responder=None)
        handler._handle_component_agent_config_set()
        handler._serve_json.assert_called_once_with(
            {"error": "component agent not configured"}, status=503
        )

    # -- missing body ------------------------------------------------------

    def test_returns_400_when_content_length_zero(self) -> None:
        """Returns 400 when Content-Length header is 0."""
        mock_responder = mock.MagicMock()
        handler = _FakeHandler(component_responder=mock_responder)
        handler.headers = {"Content-Length": "0"}
        handler._handle_component_agent_config_set()
        handler._serve_json.assert_called_once_with(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "missing request body",
                }
            },
            status=400,
        )

    # -- invalid JSON ------------------------------------------------------

    def test_returns_400_when_invalid_json(self) -> None:
        """Returns 400 when the request body is not valid JSON."""
        mock_responder = mock.MagicMock()
        handler = _FakeHandler(component_responder=mock_responder)
        handler.headers = {"Content-Length": "9"}
        handler.rfile.read.return_value = b"not-json!"
        handler._handle_component_agent_config_set()
        handler.rfile.read.assert_called_once_with(9)
        handler._serve_json.assert_called_once_with(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "invalid JSON body",
                }
            },
            status=400,
        )

    # -- body not a dict ---------------------------------------------------

    def test_returns_400_when_body_not_a_dict(self) -> None:
        """Returns 400 when the parsed JSON is not a dict."""
        mock_responder = mock.MagicMock()
        handler = _FakeHandler(component_responder=mock_responder)
        handler.headers = {"Content-Length": "2"}
        handler.rfile.read.return_value = b"[]"
        handler._handle_component_agent_config_set()
        handler._serve_json.assert_called_once_with(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "request body must be a JSON object",
                }
            },
            status=400,
        )

    # -- missing 'updates' key ---------------------------------------------

    def test_returns_400_when_missing_updates_key(self) -> None:
        """Returns 400 when the JSON body has no 'updates' key."""
        mock_responder = mock.MagicMock()
        handler = _FakeHandler(component_responder=mock_responder)
        body = json.dumps({"other": "value"}).encode("utf-8")
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile.read.return_value = body
        handler._handle_component_agent_config_set()
        handler._serve_json.assert_called_once_with(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "missing 'updates' key in request body",
                }
            },
            status=400,
        )

    # -- 'updates' not a dict ----------------------------------------------

    def test_returns_400_when_updates_not_a_dict(self) -> None:
        """Returns 400 when the 'updates' value is not a dict."""
        mock_responder = mock.MagicMock()
        handler = _FakeHandler(component_responder=mock_responder)
        body = json.dumps({"updates": [1, 2, 3]}).encode("utf-8")
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile.read.return_value = body
        handler._handle_component_agent_config_set()
        handler._serve_json.assert_called_once_with(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "'updates' must be a dict",
                }
            },
            status=400,
        )

    # -- valid updates → success -------------------------------------------

    def test_delegates_and_returns_200_on_success(self) -> None:
        """Delegates to config_set_direct and returns the result with 200."""
        mock_responder = mock.MagicMock()
        result = {"applied": {"triage.on_ingest": [True, False]}}
        mock_responder.config_set_direct.return_value = result
        handler = _FakeHandler(component_responder=mock_responder)
        updates = {"triage.on_ingest": False}
        body = json.dumps({"updates": updates}).encode("utf-8")
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile.read.return_value = body
        handler._handle_component_agent_config_set()
        mock_responder.config_set_direct.assert_called_once_with(updates)
        handler._serve_json.assert_called_once_with(result, status=200)

    # -- valid updates → responder error -----------------------------------

    def test_returns_400_when_responder_returns_error(self) -> None:
        """Returns 400 when config_set_direct result contains an 'error' key."""
        mock_responder = mock.MagicMock()
        error_result = {"error": {"code": "invalid_key", "message": "unknown key"}}
        mock_responder.config_set_direct.return_value = error_result
        handler = _FakeHandler(component_responder=mock_responder)
        updates = {"nonexistent.key": "value"}
        body = json.dumps({"updates": updates}).encode("utf-8")
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile.read.return_value = body
        handler._handle_component_agent_config_set()
        mock_responder.config_set_direct.assert_called_once_with(updates)
        handler._serve_json.assert_called_once_with(error_result, status=400)
