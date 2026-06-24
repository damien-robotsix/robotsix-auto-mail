"""Component-agent responder: lifecycle and request dispatch.

Provides ``ComponentAgentResponder`` (the ``on_request`` handler) plus
``start_component_responder`` / ``stop_component_responder`` lifecycle
functions that mirror the pattern in ``server/board_agent.py``.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from robotsix_auto_mail.config.model import MailConfig

logger = logging.getLogger(__name__)


class _ConfigHolder:
    """Mutable wrapper around a ``MailConfig`` for live ``config-set``."""

    def __init__(self, config: MailConfig) -> None:
        self.config = config


class ComponentAgentResponder:
    """Handles ``on_request`` dispatch for the component agent.

    Holds a mutable config holder so ``config-set`` can apply live
    without corrupting the running config; ``monitor`` and ``config-get``
    read from the holder.
    """

    def __init__(self, config: MailConfig) -> None:
        self._holder = _ConfigHolder(config)

    # -- on_request dispatch ------------------------------------------------

    def on_request(self, request: Any) -> Any:
        """Dispatch on ``request.body["kind"]``.

        Returns a ``Response`` or ``Error`` message from
        ``robotsix_agent_comm.protocol``.
        """
        # Lazy import — all SDK symbols are guarded.
        from robotsix_agent_comm.protocol import Error, Response

        body = getattr(request, "body", None)
        if not isinstance(body, dict):
            return Error.to(
                request,
                code="invalid_request",
                message="Request body must be a JSON object",
            )

        kind = body.get("kind")
        if kind == "monitor":
            return Response.to(request, body=self._monitor())
        elif kind == "config-get":
            return Response.to(request, body=self._config_get())
        elif kind == "config-set":
            return self._config_set(request)
        else:
            return Error.to(
                request,
                code="unknown_kind",
                message=f"Unknown request kind: {kind!r}. "
                f"Supported kinds: monitor, config-get, config-set",
            )

    # -- monitor ------------------------------------------------------------

    def _monitor(self) -> dict[str, Any]:
        """Assemble genuine live telemetry from the running process.

        Returns DB reachability, record/untriaged/triage counts, watermark
        states, per-account board summary, and a capabilities list.
        """
        from robotsix_auto_mail.db import (
            get_watermark,
            init_db,
            list_records,
            list_untriaged_records,
        )

        cfg = self._holder.config

        telemetry: dict[str, Any] = {
            "agent_id": cfg.component_agent_id,
            "capabilities": ["monitor", "config-get", "config-set"],
            "db": {},
            "watermarks": {},
        }

        # DB reachability + counts
        try:
            conn = init_db(cfg.db_path, skip_migrations=True)
        except Exception as exc:
            telemetry["db"] = {"reachable": False, "error": str(exc)}
            return telemetry

        try:
            records = list_records(conn)
            untriaged = list_untriaged_records(conn)
            telemetry["db"] = {
                "reachable": True,
                "path": cfg.db_path,
                "record_count": len(records),
                "untriaged_count": len(untriaged),
            }

            # Watermark states
            for key in (
                "imap_uid",
                "reconcile:state",
                "triage_run:state",
                "batch_op:state",
            ):
                wm = get_watermark(conn, key)
                telemetry["watermarks"][key] = wm
        finally:
            conn.close()

        # Per-account board summary (single-account for now)
        telemetry["board"] = {
            "accounts": 1,
            "default_account_id": "default",
        }
        telemetry["config_summary"] = {
            "archive_enabled": cfg.archive_enabled,
            "triage_on_ingest": cfg.triage_on_ingest,
            "calendar_enabled": cfg.calendar_enabled,
            "component_agent_enabled": cfg.component_agent_enabled,
        }

        return telemetry

    # -- config-get ---------------------------------------------------------

    def _config_get(self) -> dict[str, Any]:
        """Return a redacted config snapshot + describe map."""
        from robotsix_auto_mail.component_agent.config_contract import (
            describe_config,
            get_config_snapshot,
        )

        cfg = self._holder.config
        return {
            "config": get_config_snapshot(cfg),
            "describe": describe_config(cfg),
        }

    # -- config-set ---------------------------------------------------------

    def _config_set(self, request: Any) -> Any:
        """Validate and apply a config update, or return an Error."""
        from robotsix_agent_comm.protocol import Error, Response

        from robotsix_auto_mail.component_agent.config_contract import (
            ConfigContractError,
            apply_config_update,
        )

        body = getattr(request, "body", None)
        updates = body.get("updates") if isinstance(body, dict) else None
        if not isinstance(updates, dict):
            return Error.to(
                request,
                code="invalid_request",
                message="config-set requires an 'updates' dict",
            )

        try:
            audit = apply_config_update(self._holder, updates)
        except ConfigContractError as exc:
            return Error.to(
                request,
                code=exc.code,
                message=exc.message,
                **exc.details,
            )

        return Response.to(request, body={"applied": _redact_audit(audit)})

    # -- capabilities -------------------------------------------------------

    def capabilities(self) -> list[str]:
        """Return the list of supported request kinds (for discovery)."""
        return ["monitor", "config-get", "config-set"]


# ---------------------------------------------------------------------------
# Lifecycle (matches server/board_agent.py pattern)
# ---------------------------------------------------------------------------


def _redact_audit(audit: dict[str, tuple[object, object]]) -> dict[str, list[Any]]:
    """Redact secret values in an audit map for safe transmission."""
    from robotsix_auto_mail.component_agent.config_contract import (
        _SECRET_FIELDS,
        _yaml_path_to_spec,
    )

    redacted_marker = "<redacted>"
    result: dict[str, list[Any]] = {}
    for key, (old, new) in audit.items():
        spec = _yaml_path_to_spec.get(key)
        if spec is not None and spec.field_name in _SECRET_FIELDS:
            result[key] = [redacted_marker, redacted_marker]
        else:
            result[key] = [old, new]
    return result


def start_component_responder(config: MailConfig) -> object | None:
    """Start the component-agent responder in a background daemon thread.

    When ``robotsix_agent_comm`` is not installed the import is caught
    and the server continues without the agent.

    Returns an opaque handle ``(thread, stop_event)`` for
    :func:`stop_component_responder`, or ``None`` when the import fails
    or the component agent is not enabled.
    """
    import inspect

    try:
        from robotsix_agent_comm.sdk import Agent
        from robotsix_agent_comm.transport import (
            BrokeredRegistry,
            NetworkedBrokerTransport,
        )
    except ImportError:
        logger.info(
            "component_agent: robotsix_agent_comm is not installed. "
            "The component agent will not be started."
        )
        return None

    from robotsix_auto_mail.component_agent.settings import ComponentAgentSettings

    settings = ComponentAgentSettings.from_config(config)
    if not settings.enabled:
        return None

    # Build TLS context if a custom CA is provided.
    import ssl

    ssl_context: ssl.SSLContext | None = None
    if settings.broker_tls_ca:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cafile=settings.broker_tls_ca)
        ssl_context = ctx

    registry = BrokeredRegistry(
        settings.broker_host,
        settings.broker_port,
        scheme="https",
        ssl_context=ssl_context,
        agent_token=settings.broker_token,
    )
    transport = NetworkedBrokerTransport(
        settings.broker_host,
        settings.broker_port,
        scheme="https",
        ssl_context=ssl_context,
        agent_token=settings.broker_token,
    )

    responder = ComponentAgentResponder(config)
    agent = Agent(
        settings.agent_id,
        registry,
        transport=transport,
        pull=True,
    )
    agent.on_request(responder.on_request)

    stop_event = threading.Event()

    def _run_agent() -> None:
        # Agent.start/stop may be sync or async — handle both.
        if inspect.iscoroutinefunction(Agent.start):
            import asyncio

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(agent.start())  # type: ignore[arg-type,func-returns-value]
                while not stop_event.is_set():
                    stop_event.wait(timeout=1.0)
                loop.run_until_complete(agent.stop())  # type: ignore[arg-type,func-returns-value]
            finally:
                loop.close()
        else:
            agent.start()
            try:
                while not stop_event.is_set():
                    stop_event.wait(timeout=1.0)
            finally:
                agent.stop()

    thread = threading.Thread(target=_run_agent, daemon=True)
    thread.start()
    return (thread, stop_event)


def stop_component_responder(handle: object | None) -> None:
    """Signal the component-agent responder to stop and join its thread.

    No-op when *handle* is ``None``.  Never raises — exceptions from the
    cleanup are caught and logged.
    """
    if handle is None:
        return
    try:
        thread, stop_event = handle  # type: ignore[misc]
        stop_event.set()  # type: ignore[has-type]
        thread.join(timeout=5.0)  # type: ignore[has-type]
    except Exception:  # noqa: S110
        # Best-effort shutdown; never let a cleanup error crash the server.
        pass
