"""Unit tests for the ComponentAgentResponder.

Covers ``_monitor`` (DB reachable/unreachable), ``_config_get``,
``config_set_direct`` (success/error), ``capabilities``, and
``_redact_audit``.
"""

from __future__ import annotations

import sqlite3
from unittest import mock

from robotsix_auto_mail.config.model import MailConfig
from robotsix_auto_mail.server._component_agent_responder import (
    ComponentAgentResponder,
    _redact_audit,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# _monitor
# ---------------------------------------------------------------------------


class TestMonitor:
    def test_returns_expected_structure(self) -> None:
        cfg = _make_config()
        responder = ComponentAgentResponder(cfg)
        with (
            mock.patch("robotsix_auto_mail.db.init_db") as mock_init,
            mock.patch("robotsix_auto_mail.db.list_records") as mock_list_records,
            mock.patch(
                "robotsix_auto_mail.db.list_untriaged_records"
            ) as mock_list_untriaged,
            mock.patch("robotsix_auto_mail.db.get_watermark") as mock_get_wm,
        ):
            mock_conn = mock.MagicMock(spec=sqlite3.Connection)
            mock_init.return_value = mock_conn
            mock_list_records.return_value = [1, 2, 3]
            mock_list_untriaged.return_value = [1]
            mock_get_wm.return_value = "wm_value"

            result = responder._monitor()

        assert result["agent_id"] == "robotsix-auto-mail"
        assert "monitor" in result["capabilities"]
        assert "config-get" in result["capabilities"]
        assert "config-set" in result["capabilities"]
        assert result["db"]["reachable"] is True
        assert result["db"]["record_count"] == 3
        assert result["db"]["untriaged_count"] == 1
        assert "watermarks" in result
        assert "board" in result
        assert "config_summary" in result
        mock_conn.close.assert_called_once()

    def test_db_unreachable_path(self) -> None:
        cfg = _make_config()
        responder = ComponentAgentResponder(cfg)
        with mock.patch("robotsix_auto_mail.db.init_db") as mock_init:
            mock_init.side_effect = RuntimeError("disk full")

            result = responder._monitor()

        assert result["db"]["reachable"] is False
        assert result["db"]["error"] == "disk full"

    def test_config_summary_reflects_config(self) -> None:
        cfg = _make_config(
            archive_enabled=False, triage_on_ingest=False, component_agent_enabled=True
        )
        responder = ComponentAgentResponder(cfg)
        with (
            mock.patch("robotsix_auto_mail.db.init_db") as mock_init,
            mock.patch("robotsix_auto_mail.db.list_records") as mock_list_records,
            mock.patch(
                "robotsix_auto_mail.db.list_untriaged_records"
            ) as mock_list_untriaged,
            mock.patch("robotsix_auto_mail.db.get_watermark") as mock_get_wm,
        ):
            mock_conn = mock.MagicMock(spec=sqlite3.Connection)
            mock_init.return_value = mock_conn
            mock_list_records.return_value = []
            mock_list_untriaged.return_value = []
            mock_get_wm.return_value = "wm_value"

            result = responder._monitor()

        assert result["config_summary"]["archive_enabled"] is False
        assert result["config_summary"]["triage_on_ingest"] is False
        assert result["config_summary"]["component_agent_enabled"] is True

    def test_watermark_keys_populated(self) -> None:
        cfg = _make_config()
        responder = ComponentAgentResponder(cfg)
        with (
            mock.patch("robotsix_auto_mail.db.init_db") as mock_init,
            mock.patch("robotsix_auto_mail.db.list_records") as mock_list_records,
            mock.patch(
                "robotsix_auto_mail.db.list_untriaged_records"
            ) as mock_list_untriaged,
            mock.patch("robotsix_auto_mail.db.get_watermark") as mock_get_wm,
        ):
            mock_conn = mock.MagicMock(spec=sqlite3.Connection)
            mock_init.return_value = mock_conn
            mock_list_records.return_value = []
            mock_list_untriaged.return_value = []
            mock_get_wm.return_value = "wm_value"

            result = responder._monitor()

        expected_keys = {
            "imap_uid",
            "reconcile:state",
            "triage_run:state",
            "batch_op:state",
        }
        assert set(result["watermarks"].keys()) == expected_keys
        for key in expected_keys:
            assert result["watermarks"][key] == "wm_value"


# ---------------------------------------------------------------------------
# _config_get
# ---------------------------------------------------------------------------


class TestConfigGet:
    def test_returns_config_and_describe(self) -> None:
        cfg = _make_config()
        responder = ComponentAgentResponder(cfg)
        result = responder._config_get()
        assert "config" in result
        assert "describe" in result
        assert isinstance(result["config"], dict)
        assert isinstance(result["describe"], dict)

    def test_password_is_redacted(self) -> None:
        cfg = _make_config(password="secret123")
        responder = ComponentAgentResponder(cfg)
        result = responder._config_get()
        assert result["config"]["auth.password"] == "<redacted>"

    def test_describe_has_settable_flag(self) -> None:
        cfg = _make_config()
        responder = ComponentAgentResponder(cfg)
        result = responder._config_get()
        desc = result["describe"]
        assert desc["triage.on_ingest"]["settable"] is True
        assert desc["imap.host"]["settable"] is False


# ---------------------------------------------------------------------------
# config_set_direct
# ---------------------------------------------------------------------------


class TestConfigSetDirect:
    def test_successful_update_returns_applied(self) -> None:
        cfg = _make_config(triage_on_ingest=True)
        responder = ComponentAgentResponder(cfg)
        result = responder.config_set_direct({"triage.on_ingest": False})
        assert "applied" in result
        assert "triage.on_ingest" in result["applied"]
        old, new = result["applied"]["triage.on_ingest"]
        assert old is True
        assert new is False

    def test_config_is_mutated_after_set(self) -> None:
        cfg = _make_config(triage_on_ingest=True, archive_enabled=False)
        responder = ComponentAgentResponder(cfg)
        responder.config_set_direct(
            {"triage.on_ingest": False, "archive.enabled": True}
        )
        assert responder._holder.config.triage_on_ingest is False
        assert responder._holder.config.archive_enabled is True

    def test_invalid_key_returns_error(self) -> None:
        cfg = _make_config()
        responder = ComponentAgentResponder(cfg)
        result = responder.config_set_direct({"nonexistent.key": "value"})
        assert "error" in result
        assert result["error"]["code"] == "invalid_key"

    def test_non_settable_key_returns_error(self) -> None:
        cfg = _make_config()
        responder = ComponentAgentResponder(cfg)
        result = responder.config_set_direct({"imap.host": "evil.com"})
        assert "error" in result
        assert result["error"]["code"] == "invalid_key"

    def test_invalid_value_returns_error(self) -> None:
        cfg = _make_config()
        responder = ComponentAgentResponder(cfg)
        result = responder.config_set_direct({"ingest.interval_minutes": "abc"})
        assert "error" in result
        assert result["error"]["code"] == "invalid_value"

    def test_non_dict_input_returns_error(self) -> None:
        cfg = _make_config()
        responder = ComponentAgentResponder(cfg)
        result = responder.config_set_direct("not a dict")  # type: ignore[arg-type]
        assert "error" in result
        assert result["error"]["code"] == "invalid_request"

    def test_secret_key_redacted_in_applied(self) -> None:
        cfg = _make_config(llm_api_key="sk-abc123")
        responder = ComponentAgentResponder(cfg)
        result = responder.config_set_direct({"llm.api_key": "sk-new"})
        assert "applied" in result
        # The returned applied should have redacted values.
        old, new = result["applied"]["llm.api_key"]
        assert old == "<redacted>"
        assert new == "<redacted>"

    def test_non_secret_key_not_redacted_in_applied(self) -> None:
        cfg = _make_config(triage_on_ingest=True)
        responder = ComponentAgentResponder(cfg)
        result = responder.config_set_direct({"triage.on_ingest": False})
        old, new = result["applied"]["triage.on_ingest"]
        assert old is True
        assert new is False


# ---------------------------------------------------------------------------
# _config_set delegates to config_set_direct
# ---------------------------------------------------------------------------


class TestConfigSet:
    def test_delegates_to_config_set_direct(self) -> None:
        cfg = _make_config(triage_on_ingest=True)
        responder = ComponentAgentResponder(cfg)
        with mock.patch.object(
            responder, "config_set_direct", wraps=responder.config_set_direct
        ) as spy:
            result = responder._config_set({"triage.on_ingest": False})
            spy.assert_called_once_with({"triage.on_ingest": False})
        assert "applied" in result


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_returns_expected_list(self) -> None:
        cfg = _make_config()
        responder = ComponentAgentResponder(cfg)
        caps = responder.capabilities()
        assert caps == ["monitor", "config-get", "config-set"]

    def test_returns_list_of_strings(self) -> None:
        cfg = _make_config()
        responder = ComponentAgentResponder(cfg)
        caps = responder.capabilities()
        for item in caps:
            assert isinstance(item, str)


# ---------------------------------------------------------------------------
# _redact_audit
# ---------------------------------------------------------------------------


class TestRedactAudit:
    def test_redacts_secret_fields(self) -> None:
        audit = {
            "llm.api_key": ("sk-old", "sk-new"),
            "langfuse.secret_key": ("lf-old", "lf-new"),
        }
        result = _redact_audit(audit)
        assert result["llm.api_key"] == ["<redacted>", "<redacted>"]
        assert result["langfuse.secret_key"] == ["<redacted>", "<redacted>"]

    def test_preserves_non_secret_fields(self) -> None:
        audit = {
            "triage.on_ingest": (True, False),
            "archive.root": ("/old", "/new"),
        }
        result = _redact_audit(audit)
        assert result["triage.on_ingest"] == [True, False]
        assert result["archive.root"] == ["/old", "/new"]

    def test_mixed_secret_and_non_secret(self) -> None:
        audit = {
            "llm.api_key": ("sk-old", "sk-new"),
            "archive.enabled": (False, True),
        }
        result = _redact_audit(audit)
        assert result["llm.api_key"] == ["<redacted>", "<redacted>"]
        assert result["archive.enabled"] == [False, True]

    def test_unknown_key_preserved_as_is(self) -> None:
        audit = {"unknown.key": ("old", "new")}
        result = _redact_audit(audit)
        assert result["unknown.key"] == ["old", "new"]

    def test_result_is_dict_of_lists(self) -> None:
        audit = {"triage.on_ingest": (True, False)}
        result = _redact_audit(audit)
        assert isinstance(result, dict)
        for val in result.values():
            assert isinstance(val, list)
            assert len(val) == 2
