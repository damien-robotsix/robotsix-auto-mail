"""Component-agent responder: lifecycle and request dispatch.

Provides ``ComponentAgentResponder`` with ``_monitor``, ``_config_get``,
``_config_set``, and ``config_set_direct`` methods for the board server's
HTTP API.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from robotsix_auto_mail._constants import (
    _BATCH_OP_STATE_KEY,
    _RECONCILE_STATE_KEY,
    _TRIAGE_RUN_STATE_KEY,
)

if TYPE_CHECKING:
    from robotsix_auto_mail.config.model import MailConfig

logger = logging.getLogger(__name__)


class _ConfigHolder:
    """Mutable wrapper around a ``MailConfig`` for live ``config-set``."""

    def __init__(self, config: MailConfig) -> None:
        self.config = config


class ComponentAgentResponder:
    """Handles component-agent requests for the HTTP API.

    Holds a mutable config holder so ``config-set`` can apply live
    without corrupting the running config; ``monitor`` and ``config-get``
    read from the holder.
    """

    def __init__(self, config: MailConfig) -> None:
        self._holder = _ConfigHolder(config)

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
            "agent_id": "robotsix-auto-mail",
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
                _RECONCILE_STATE_KEY,
                _TRIAGE_RUN_STATE_KEY,
                _BATCH_OP_STATE_KEY,
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

    def _config_set(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Validate and apply a config update; return result dict.

        Delegates to :meth:`config_set_direct`.
        """
        return self.config_set_direct(updates)

    def config_set_direct(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Apply config updates; return {"applied": <audit>} or {"error": {...}}."""
        from robotsix_auto_mail.component_agent.config_contract import (
            ConfigContractError,
            apply_config_update,
        )

        if not isinstance(updates, dict):
            return {
                "error": {
                    "code": "invalid_request",
                    "message": "config-set requires an 'updates' dict",
                }
            }

        try:
            audit = apply_config_update(self._holder, updates)
        except ConfigContractError as exc:
            return {"error": {"code": exc.code, "message": exc.message, **exc.details}}

        return {"applied": _redact_audit(audit)}

    # -- capabilities -------------------------------------------------------

    def capabilities(self) -> list[str]:
        """Return the list of supported request kinds (for discovery)."""
        return ["monitor", "config-get", "config-set"]


# ---------------------------------------------------------------------------
# Helpers
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
