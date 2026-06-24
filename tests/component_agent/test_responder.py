"""Unit tests for ``robotsix_auto_mail.component_agent.responder``."""

from __future__ import annotations

import threading
from unittest import mock

import pytest

from robotsix_auto_mail.component_agent.responder import (
    ComponentAgentResponder,
    start_component_responder,
    stop_component_responder,
)
from robotsix_auto_mail.config import ConfigurationError, MailConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Fake Response/Error classes for testing on_request dispatch without
# requiring the robotsix-agent-comm package to be installed.
class _FakeResponse:
    def __init__(self, body: object = None) -> None:
        self.body = body

class _FakeError:
    def __init__(self, body: object = None) -> None:
        self.body = body

_FAKE_RESPONSE_CLS = _FakeResponse
_FAKE_ERROR_CLS = _FakeError
_FAKE_PROTOCOL = mock.MagicMock()
_FAKE_PROTOCOL.Response = _FAKE_RESPONSE_CLS
_FAKE_PROTOCOL.Error = _FAKE_ERROR_CLS
# ``to`` is a classmethod on both Response and Error that creates
# instances — mock it per-class so isinstance checks pass.


def _fake_response_to(cls, request: object, body: object = None) -> _FakeResponse:  # type: ignore[no-untyped-def]
    return cls(body=body)


def _fake_error_to(cls, request: object, code: str = "", message: str = "", **kwargs: object) -> _FakeError:  # type: ignore[no-untyped-def]
    body: dict[str, object] = {"code": code, "message": message}
    body.update(kwargs)
    return cls(body=body)


_FAKE_RESPONSE_CLS.to = classmethod(_fake_response_to)  # type: ignore[assignment]
_FAKE_ERROR_CLS.to = classmethod(_fake_error_to)  # type: ignore[assignment]



def _make_config(**overrides: object) -> MailConfig:
    kwargs: dict[str, object] = dict(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
    )
    kwargs.update(overrides)
    return MailConfig(**kwargs)  # type: ignore[arg-type]


def _make_enabled_config() -> MailConfig:
    return _make_config(
        component_agent_enabled=True,
        component_agent_broker_host="broker.example.com",
        component_agent_broker_token="tok-123",
    )


def _make_fake_request(body: dict) -> mock.MagicMock:
    """Build a fake agent-comm Request with the given body dict."""
    req = mock.MagicMock()
    req.body = body
    return req


# ---------------------------------------------------------------------------
# ComponentAgentResponder — on_request dispatch
# ---------------------------------------------------------------------------


class TestOnRequestDispatch:
    """on_request dispatches on request.body["kind"]."""

    _PROTOCOL_PATCH = mock.patch.dict(
        "sys.modules",
        {"robotsix_agent_comm.protocol": _FAKE_PROTOCOL},
    )

    def test_monitor_returns_response(self) -> None:
        cfg = _make_config(
            db_path=":memory:",
            component_agent_enabled=True,
            component_agent_broker_host="broker.example.com",
            component_agent_broker_token="tok-123",
        )
        responder = ComponentAgentResponder(cfg)
        req = _make_fake_request({"kind": "monitor"})

        with self._PROTOCOL_PATCH:
            result = responder.on_request(req)

        response_cls = _FAKE_RESPONSE_CLS
        assert isinstance(result, response_cls)
        assert "db" in result.body
        assert "capabilities" in result.body
        assert result.body["capabilities"] == [
            "monitor",
            "config-get",
            "config-set",
        ]

    def test_config_get_returns_snapshot_and_describe(self) -> None:
        cfg = _make_enabled_config()
        responder = ComponentAgentResponder(cfg)
        req = _make_fake_request({"kind": "config-get"})

        with self._PROTOCOL_PATCH:
            result = responder.on_request(req)

        assert isinstance(result, _FAKE_RESPONSE_CLS)
        assert "config" in result.body
        assert "describe" in result.body
        assert result.body["config"]["auth.password"] == "<redacted>"

    def test_config_set_applies_valid_update(self) -> None:
        cfg = _make_enabled_config()
        responder = ComponentAgentResponder(cfg)
        req = _make_fake_request(
            {
                "kind": "config-set",
                "updates": {"triage.on_ingest": False},
            }
        )

        with self._PROTOCOL_PATCH:
            result = responder.on_request(req)

        assert isinstance(result, _FAKE_RESPONSE_CLS)
        assert "applied" in result.body
        assert result.body["applied"]["triage.on_ingest"] == [True, False]
        assert responder._holder.config.triage_on_ingest is False

    def test_config_set_rejects_invalid_key(self) -> None:
        cfg = _make_enabled_config()
        responder = ComponentAgentResponder(cfg)
        req = _make_fake_request(
            {
                "kind": "config-set",
                "updates": {"imap.host": "evil.com"},
            }
        )

        with self._PROTOCOL_PATCH:
            result = responder.on_request(req)

        assert isinstance(result, _FAKE_ERROR_CLS)
        assert result.body["code"] == "invalid_key"

    def test_config_set_missing_updates_dict(self) -> None:
        cfg = _make_enabled_config()
        responder = ComponentAgentResponder(cfg)
        req = _make_fake_request({"kind": "config-set"})

        with self._PROTOCOL_PATCH:
            result = responder.on_request(req)

        assert isinstance(result, _FAKE_ERROR_CLS)
        assert result.body["code"] == "invalid_request"

    def test_unknown_kind_returns_error(self) -> None:
        cfg = _make_enabled_config()
        responder = ComponentAgentResponder(cfg)
        req = _make_fake_request({"kind": "bogus"})

        with self._PROTOCOL_PATCH:
            result = responder.on_request(req)

        assert isinstance(result, _FAKE_ERROR_CLS)
        assert result.body["code"] == "unknown_kind"

    def test_non_dict_body_returns_error(self) -> None:
        cfg = _make_enabled_config()
        responder = ComponentAgentResponder(cfg)
        req = mock.MagicMock()
        req.body = "not-a-dict"

        with self._PROTOCOL_PATCH:
            result = responder.on_request(req)

        assert isinstance(result, _FAKE_ERROR_CLS)
        assert result.body["code"] == "invalid_request"


# ---------------------------------------------------------------------------
# ComponentAgentResponder — monitor with real DB
# ---------------------------------------------------------------------------


class TestMonitorWithDb:
    """monitor returns genuine counts/watermarks against a real temp DB."""

    def test_monitor_against_temp_db(self) -> None:
        """Use an in-memory SQLite connection with schema applied."""
        from robotsix_auto_mail.db import init_db, insert_record, set_watermark
        from tests.conftest import _make_record

        conn = init_db(":memory:")
        try:
            rec = _make_record()
            insert_record(conn, rec)
            set_watermark(conn, "imap_uid", "42")
            set_watermark(conn, "reconcile:state", "idle")

            cfg = _make_config(
                db_path=":memory:",
                component_agent_enabled=True,
                component_agent_broker_host="broker.example.com",
                component_agent_broker_token="tok-123",
            )
            responder = ComponentAgentResponder(cfg)

            with mock.patch(
                "robotsix_auto_mail.db.init_db",
                return_value=conn,
            ):
                result = responder._monitor()

            assert result["db"]["reachable"] is True
            assert result["db"]["record_count"] == 1
            assert result["db"]["untriaged_count"] == 1
            assert result["watermarks"]["imap_uid"] == "42"
            assert result["watermarks"]["reconcile:state"] == "idle"
            assert result["capabilities"] == [
                "monitor",
                "config-get",
                "config-set",
            ]
        finally:
            conn.close()

    def test_monitor_handles_db_error(self) -> None:
        cfg = _make_config(
            db_path="/nonexistent/path/mail.db",
            component_agent_enabled=True,
            component_agent_broker_host="broker.example.com",
            component_agent_broker_token="tok-123",
        )
        responder = ComponentAgentResponder(cfg)
        result = responder._monitor()
        assert result["db"]["reachable"] is False
        assert "error" in result["db"]


# ---------------------------------------------------------------------------
# start_component_responder — lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    """start/stop_component_responder lifecycle."""

    def test_returns_none_when_disabled(self) -> None:
        cfg = _make_config(component_agent_enabled=False)
        handle = start_component_responder(cfg)
        assert handle is None

    def test_returns_none_when_import_fails(self) -> None:
        cfg = _make_enabled_config()
        with mock.patch.dict(
            "sys.modules",
            {
                "robotsix_agent_comm": None,
                "robotsix_agent_comm.sdk": None,
                "robotsix_agent_comm.transport": None,
                "robotsix_agent_comm.protocol": None,
            },
        ):
            handle = start_component_responder(cfg)
        assert handle is None

    def test_returns_handle_when_enabled_and_importable(self) -> None:
        cfg = _make_enabled_config()

        # Build fake SDK objects to inject into sys.modules so the
        # function's own lazy imports resolve to mocks, not real objects.
        fake_agent = mock.MagicMock()
        fake_agent.start.return_value = None
        fake_agent.stop.return_value = None

        fake_agent_cls = mock.MagicMock(return_value=fake_agent)
        fake_brokered_registry = mock.MagicMock()
        fake_networked_transport = mock.MagicMock()

        fake_sdk = mock.MagicMock(Agent=fake_agent_cls)
        fake_transport = mock.MagicMock(
            BrokeredRegistry=fake_brokered_registry,
            NetworkedBrokerTransport=fake_networked_transport,
        )
        fake_protocol = mock.MagicMock()

        with mock.patch.dict(
            "sys.modules",
            {
                "robotsix_agent_comm": mock.MagicMock(),
                "robotsix_agent_comm.sdk": fake_sdk,
                "robotsix_agent_comm.transport": fake_transport,
                "robotsix_agent_comm.protocol": fake_protocol,
            },
        ):
            handle = start_component_responder(cfg)

        assert handle is not None
        thread, stop_event = handle
        assert isinstance(thread, threading.Thread)
        assert isinstance(stop_event, threading.Event)
        assert thread.daemon is True
        assert thread.is_alive()

        stop_event.set()
        thread.join(timeout=5.0)

    def test_stop_component_responder_none_is_noop(self) -> None:
        stop_component_responder(None)

    def test_stop_component_responder_signals_and_joins(self) -> None:
        stop_event = threading.Event()
        thread = threading.Thread(
            target=lambda: stop_event.wait(),
            daemon=True,
        )
        thread.start()

        stop_component_responder((thread, stop_event))
        assert stop_event.is_set()
        assert not thread.is_alive()

    def test_stop_handles_cleanup_exceptions(self) -> None:
        stop_event = threading.Event()
        bad_thread = mock.MagicMock(spec=threading.Thread)
        bad_thread.join.side_effect = RuntimeError("boom")
        stop_component_responder((bad_thread, stop_event))
        assert stop_event.is_set()


# ---------------------------------------------------------------------------
# ConfigurationError when token missing
# ---------------------------------------------------------------------------


class TestConfigInvariant:
    """Token-required-when-enabled invariant."""

    def test_enabled_without_token_raises(self) -> None:
        with pytest.raises(ConfigurationError) as exc_info:
            MailConfig(
                imap_host="h",
                smtp_host="h",
                username="u",
                password="p",
                component_agent_enabled=True,
            )
        assert "component_agent_broker_token" in str(exc_info.value)

    def test_enabled_without_host_raises(self) -> None:
        with pytest.raises(ConfigurationError) as exc_info:
            MailConfig(
                imap_host="h",
                smtp_host="h",
                username="u",
                password="p",
                component_agent_enabled=True,
                component_agent_broker_token="tok",
            )
        assert "component_agent_broker_host" in str(exc_info.value)

    def test_enabled_with_both_is_ok(self) -> None:
        cfg = MailConfig(
            imap_host="h",
            smtp_host="h",
            username="u",
            password="p",
            component_agent_enabled=True,
            component_agent_broker_host="broker.example.com",
            component_agent_broker_token="tok",
        )
        assert cfg.component_agent_enabled is True

    def test_token_redacted_in_repr(self) -> None:
        cfg = MailConfig(
            imap_host="h",
            smtp_host="h",
            username="u",
            password="p",
            component_agent_enabled=True,
            component_agent_broker_host="broker.example.com",
            component_agent_broker_token="secret-token",
        )
        r = repr(cfg)
        assert "secret-token" not in r
        assert "component_agent_broker_token=<redacted>" in r

    def test_disabled_needs_no_token(self) -> None:
        cfg = MailConfig(
            imap_host="h",
            smtp_host="h",
            username="u",
            password="p",
            component_agent_enabled=False,
        )
        assert cfg.component_agent_broker_token == ""
