"""Tests for the component-agent HTTP API routes."""

from __future__ import annotations

import json
import tempfile
from http.server import HTTPServer
from typing import Any
from urllib.request import Request

import pytest

pytest.importorskip("robotsix_agent_comm")

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.server import make_board_handler
from robotsix_auto_mail.server._component_agent_responder import ComponentAgentResponder


def _make_config(**overrides: object) -> MailConfig:
    kwargs: dict[str, object] = dict(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        component_agent_enabled=True,
    )
    kwargs.update(overrides)
    return MailConfig(**kwargs)  # type: ignore[arg-type]


def _start_server_with_responder(
    db_path: str, config: MailConfig | None = None
) -> tuple[HTTPServer, int]:
    """Start a test server wired with a ComponentAgentResponder."""
    import threading

    if config is None:
        config = _make_config(db_path=db_path)

    responder = ComponentAgentResponder(config)
    handler = make_board_handler(
        db_path, mail_config=config, component_responder=responder
    )
    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def _start_server_without_responder(
    db_path: str, config: MailConfig | None = None
) -> tuple[HTTPServer, int]:
    """Start a test server WITHOUT a ComponentAgentResponder (responder=None)."""
    import threading

    if config is None:
        config = _make_config(db_path=db_path)

    handler = make_board_handler(db_path, mail_config=config, component_responder=None)
    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def _get_json(port: int, path: str) -> tuple[int, dict[str, Any]]:
    """GET *path* and return (status, parsed JSON body).

    Uses a custom opener that captures error responses instead of raising.
    """
    import urllib.request

    class _NoRaise(urllib.request.HTTPDefaultErrorHandler):
        # mypy doesn't understand that we're intentionally returning `fp`
        # instead of raising; the concrete return type is immaterial.
        def http_error_default(self, req, fp, code, msg, hdrs):  # type: ignore[no-untyped-def]
            return fp

    opener = urllib.request.build_opener(_NoRaise())
    resp = opener.open(f"http://127.0.0.1:{port}{path}")
    body = resp.read().decode("utf-8")
    return resp.status, json.loads(body)


def _post_json(
    port: int, path: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """POST JSON *payload* to *path* and return (status, parsed JSON body).

    Uses a custom opener that captures error responses instead of raising.
    """
    import urllib.request

    class _NoRaise(urllib.request.HTTPDefaultErrorHandler):
        # mypy doesn't understand that we're intentionally returning `fp`
        # instead of raising; the concrete return type is immaterial.
        def http_error_default(self, req, fp, code, msg, hdrs):  # type: ignore[no-untyped-def]
            return fp

    data = json.dumps(payload).encode("utf-8")
    req = Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRaise())
    resp = opener.open(req)
    body = resp.read().decode("utf-8")
    return resp.status, json.loads(body)


# ---------------------------------------------------------------------------
# GET /api/component-agent/monitor
# ---------------------------------------------------------------------------


class TestMonitor:
    def test_returns_200_with_expected_keys(self) -> None:
        """GET /monitor returns 200 with agent_id, capabilities, db keys."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        try:
            server, port = _start_server_with_responder(db_path)
            try:
                status, body = _get_json(port, "/api/component-agent/monitor")
                assert status == 200
                assert "agent_id" in body
                assert body["agent_id"] == "robotsix-auto-mail"
                assert "capabilities" in body
                assert "monitor" in body["capabilities"]
                assert "config-get" in body["capabilities"]
                assert "config-set" in body["capabilities"]
                assert "db" in body
            finally:
                server.shutdown()
        finally:
            os.unlink(db_path)

    def test_returns_503_when_responder_is_none(self) -> None:
        """GET /monitor returns 503 when component_responder is None."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        try:
            server, port = _start_server_without_responder(db_path)
            try:
                status, body = _get_json(port, "/api/component-agent/monitor")
                assert status == 503
                assert body == {"error": "component agent not configured"}
            finally:
                server.shutdown()
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# GET /api/component-agent/config
# ---------------------------------------------------------------------------


class TestConfigGet:
    def test_returns_200_with_config_and_describe(self) -> None:
        """GET /config returns 200 with 'config' and 'describe' keys."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        try:
            server, port = _start_server_with_responder(db_path)
            try:
                status, body = _get_json(port, "/api/component-agent/config")
                assert status == 200
                assert "config" in body
                assert "describe" in body
                assert isinstance(body["config"], dict)
                assert isinstance(body["describe"], dict)
            finally:
                server.shutdown()
        finally:
            os.unlink(db_path)

    def test_password_is_redacted_in_config(self) -> None:
        """Passwords are redacted in the config snapshot."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        try:
            config = _make_config(db_path=db_path, password="secret123")
            server, port = _start_server_with_responder(db_path, config)
            try:
                status, body = _get_json(port, "/api/component-agent/config")
                assert status == 200
                assert body["config"]["auth.password"] == "<redacted>"
            finally:
                server.shutdown()
        finally:
            os.unlink(db_path)

    def test_returns_503_when_responder_is_none(self) -> None:
        """GET /config returns 503 when component_responder is None."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        try:
            server, port = _start_server_without_responder(db_path)
            try:
                status, body = _get_json(port, "/api/component-agent/config")
                assert status == 503
                assert body == {"error": "component agent not configured"}
            finally:
                server.shutdown()
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# POST /api/component-agent/config
# ---------------------------------------------------------------------------


class TestConfigSet:
    def test_applies_valid_update(self) -> None:
        """POST /config with valid updates returns 200 and applied audit."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        try:
            config = _make_config(db_path=db_path, triage_on_ingest=True)
            server, port = _start_server_with_responder(db_path, config)
            try:
                status, body = _post_json(
                    port,
                    "/api/component-agent/config",
                    {"updates": {"triage.on_ingest": False}},
                )
                assert status == 200
                assert "applied" in body
                assert "triage.on_ingest" in body["applied"]
                old, new = body["applied"]["triage.on_ingest"]
                assert old is True
                assert new is False
            finally:
                server.shutdown()
        finally:
            os.unlink(db_path)

    def test_rejects_unknown_key(self) -> None:
        """POST /config with an unknown key returns 400."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        try:
            server, port = _start_server_with_responder(db_path)
            try:
                status, body = _post_json(
                    port,
                    "/api/component-agent/config",
                    {"updates": {"nonexistent.key": "value"}},
                )
                assert status == 400
                assert "error" in body
                assert body["error"]["code"] == "invalid_key"
            finally:
                server.shutdown()
        finally:
            os.unlink(db_path)

    def test_rejects_non_settable_key(self) -> None:
        """POST /config with a non-settable key returns 400."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        try:
            server, port = _start_server_with_responder(db_path)
            try:
                status, body = _post_json(
                    port,
                    "/api/component-agent/config",
                    {"updates": {"imap.host": "evil.com"}},
                )
                assert status == 400
                assert "error" in body
                assert body["error"]["code"] == "invalid_key"
            finally:
                server.shutdown()
        finally:
            os.unlink(db_path)

    def test_missing_updates_key_returns_400(self) -> None:
        """POST /config without 'updates' key returns 400."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        try:
            server, port = _start_server_with_responder(db_path)
            try:
                status, body = _post_json(
                    port,
                    "/api/component-agent/config",
                    {"wrong_key": "value"},
                )
                assert status == 400
                assert "error" in body
                assert body["error"]["code"] == "invalid_request"
            finally:
                server.shutdown()
        finally:
            os.unlink(db_path)

    def test_missing_body_returns_400(self) -> None:
        """POST /config with no body returns 400."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        try:
            server, port = _start_server_with_responder(db_path)
            try:
                # Send a POST with empty body by using a different approach
                import http.client

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("POST", "/api/component-agent/config", body=b"")
                resp = conn.getresponse()
                body_data = resp.read().decode("utf-8")
                status = resp.status
                conn.close()
                body = json.loads(body_data)
                assert status == 400
                assert "error" in body
            finally:
                server.shutdown()
        finally:
            os.unlink(db_path)

    def test_invalid_json_body_returns_400(self) -> None:
        """POST /config with invalid JSON returns 400."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        try:
            server, port = _start_server_with_responder(db_path)
            try:
                import http.client

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "POST",
                    "/api/component-agent/config",
                    body=b"not json",
                    headers={"Content-Type": "application/json"},
                )
                resp = conn.getresponse()
                body_data = resp.read().decode("utf-8")
                status = resp.status
                conn.close()
                body = json.loads(body_data)
                assert status == 400
                assert "error" in body
                assert body["error"]["code"] == "invalid_request"
            finally:
                server.shutdown()
        finally:
            os.unlink(db_path)

    def test_returns_503_when_responder_is_none(self) -> None:
        """POST /config returns 503 when component_responder is None."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        try:
            server, port = _start_server_without_responder(db_path)
            try:
                status, body = _post_json(
                    port,
                    "/api/component-agent/config",
                    {"updates": {"triage.on_ingest": False}},
                )
                assert status == 503
                assert body == {"error": "component agent not configured"}
            finally:
                server.shutdown()
        finally:
            os.unlink(db_path)
