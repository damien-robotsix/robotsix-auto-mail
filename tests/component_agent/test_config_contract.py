"""Unit tests for ``robotsix_auto_mail.component_agent.config_contract``."""

from __future__ import annotations

import logging

import pytest

from robotsix_auto_mail.component_agent.config_contract import (
    SETTABLE_KEYS,
    ConfigContractError,
    apply_config_update,
    describe_config,
    get_config_snapshot,
    validate_config_update,
)
from robotsix_auto_mail.config import MailConfig, schema

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: object) -> MailConfig:
    kwargs: dict[str, object] = dict(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
    )
    kwargs.update(overrides)
    return MailConfig(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# get_config_snapshot — redaction
# ---------------------------------------------------------------------------


class TestSnapshot:
    """get_config_snapshot returns a flat dotted-path map with secrets redacted."""

    def test_all_keys_present(self) -> None:
        cfg = _make_config()
        snap = get_config_snapshot(cfg)
        spec_paths = {s.yaml_path for s in schema._FIELD_SPECS}
        assert set(snap.keys()) == spec_paths

    def test_password_redacted(self) -> None:
        cfg = _make_config(password="secret123")
        snap = get_config_snapshot(cfg)
        assert snap["auth.password"] == "<redacted>"

    def test_llm_api_key_redacted(self) -> None:
        cfg = _make_config(llm_api_key="sk-or-v1-abc")
        snap = get_config_snapshot(cfg)
        assert snap["llm.api_key"] == "<redacted>"

    def test_board_agent_api_token_redacted(self) -> None:
        cfg = _make_config(board_agent_api_token="tok123")
        snap = get_config_snapshot(cfg)
        assert snap["board_agent.api_token"] == "<redacted>"

    def test_calendar_broker_token_redacted(self) -> None:
        cfg = _make_config(calendar_broker_token="tok456")
        snap = get_config_snapshot(cfg)
        assert snap["calendar.broker_token"] == "<redacted>"

    def test_component_agent_broker_token_redacted(self) -> None:
        cfg = _make_config(
            component_agent_enabled=True,
            component_agent_broker_host="broker.example.com",
            component_agent_broker_token="tok789",
        )
        snap = get_config_snapshot(cfg)
        assert snap["component_agent.broker_token"] == "<redacted>"

    def test_non_secret_fields_visible(self) -> None:
        cfg = _make_config(archive_enabled=True, triage_on_ingest=False)
        snap = get_config_snapshot(cfg)
        assert snap["archive.enabled"] is True
        assert snap["triage.on_ingest"] is False


# ---------------------------------------------------------------------------
# describe_config
# ---------------------------------------------------------------------------


class TestDescribe:
    """describe_config returns metadata for every field."""

    def test_settable_flag(self) -> None:
        cfg = _make_config()
        desc = describe_config(cfg)
        for key, info in desc.items():
            assert info["settable"] == (key in SETTABLE_KEYS)
            assert "value" in info
            assert "kind" in info

    def test_secret_values_redacted_in_describe(self) -> None:
        cfg = _make_config(password="secret123", llm_api_key="sk-abc")
        desc = describe_config(cfg)
        assert desc["auth.password"]["value"] == "<redacted>"
        assert desc["llm.api_key"]["value"] == "<redacted>"


# ---------------------------------------------------------------------------
# validate_config_update
# ---------------------------------------------------------------------------


class TestValidate:
    """validate_config_update validates without mutating."""

    def test_unknown_key_raises(self) -> None:
        cfg = _make_config()
        with pytest.raises(ConfigContractError) as exc_info:
            validate_config_update(cfg, {"no.such.key": 1})
        assert exc_info.value.code == "invalid_key"

    def test_non_settable_key_raises(self) -> None:
        cfg = _make_config()
        with pytest.raises(ConfigContractError) as exc_info:
            validate_config_update(cfg, {"imap.host": "new.example.com"})
        assert exc_info.value.code == "invalid_key"

    def test_valid_update_returns_audit_map(self) -> None:
        cfg = _make_config(triage_on_ingest=True)
        audit = validate_config_update(cfg, {"triage.on_ingest": False})
        assert audit == {"triage.on_ingest": (True, False)}

    def test_multiple_valid_keys(self) -> None:
        cfg = _make_config(
            triage_on_ingest=True,
            archive_enabled=True,
            calendar_enabled=True,
        )
        audit = validate_config_update(
            cfg,
            {
                "triage.on_ingest": False,
                "archive.enabled": False,
                "calendar.enabled": False,
            },
        )
        assert audit == {
            "triage.on_ingest": (True, False),
            "archive.enabled": (True, False),
            "calendar.enabled": (True, False),
        }

    def test_invalid_bool_value_raises(self) -> None:
        cfg = _make_config()
        with pytest.raises(ConfigContractError) as exc_info:
            validate_config_update(cfg, {"triage.on_ingest": "not-a-bool"})
        assert exc_info.value.code == "invalid_value"

    def test_invalid_int_value_raises(self) -> None:
        cfg = _make_config()
        with pytest.raises(ConfigContractError) as exc_info:
            validate_config_update(cfg, {"ingest.interval_minutes": "abc"})
        assert exc_info.value.code == "invalid_value"

    def test_bool_as_int_raises(self) -> None:
        cfg = _make_config()
        with pytest.raises(ConfigContractError) as exc_info:
            validate_config_update(cfg, {"ingest.interval_minutes": True})
        assert exc_info.value.code == "invalid_value"

    def test_string_coercion_bool_true(self) -> None:
        cfg = _make_config(triage_on_ingest=False)
        audit = validate_config_update(cfg, {"triage.on_ingest": "true"})
        assert audit == {"triage.on_ingest": (False, True)}

    def test_string_coercion_bool_false(self) -> None:
        cfg = _make_config(triage_on_ingest=True)
        audit = validate_config_update(cfg, {"triage.on_ingest": "false"})
        assert audit == {"triage.on_ingest": (True, False)}

    def test_does_not_mutate_original_config(self) -> None:
        cfg = _make_config(triage_on_ingest=True)
        validate_config_update(cfg, {"triage.on_ingest": False})
        assert cfg.triage_on_ingest is True  # unchanged

    def test_invariant_violation_is_caught(self) -> None:
        cfg = _make_config()
        with pytest.raises(ConfigContractError) as exc_info:
            validate_config_update(cfg, {"triage.on_ingest": 42})
        assert exc_info.value.code == "invalid_value"


# ---------------------------------------------------------------------------
# apply_config_update
# ---------------------------------------------------------------------------


class _FakeHolder:
    """Minimal config holder for testing apply_config_update."""

    def __init__(self, config: MailConfig) -> None:
        self.config = config


class TestApply:
    """apply_config_update validates, applies, and logs."""

    def test_applies_valid_update(self) -> None:
        cfg = _make_config(triage_on_ingest=True)
        holder = _FakeHolder(cfg)
        audit = apply_config_update(holder, {"triage.on_ingest": False})
        assert audit == {"triage.on_ingest": (True, False)}
        assert holder.config.triage_on_ingest is False

    def test_rejects_invalid_update_without_mutating(self) -> None:
        cfg = _make_config()
        holder = _FakeHolder(cfg)
        with pytest.raises(ConfigContractError):
            apply_config_update(holder, {"no.such.key": 1})
        # Original unchanged.
        assert holder.config is cfg

    def test_audit_log_emitted(self, caplog: pytest.LogCaptureFixture) -> None:
        cfg = _make_config(triage_on_ingest=True)
        holder = _FakeHolder(cfg)
        logger_name = "robotsix_auto_mail.component_agent.config_contract"
        with caplog.at_level(logging.INFO, logger=logger_name):
            apply_config_update(holder, {"triage.on_ingest": False})
        assert "config-set applied" in caplog.text
        assert "triage.on_ingest" in caplog.text

    def test_secret_values_redacted_in_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        cfg = _make_config(llm_api_key="sk-secret-123")
        holder = _FakeHolder(cfg)
        logger_name = "robotsix_auto_mail.component_agent.config_contract"
        with caplog.at_level(logging.INFO, logger=logger_name):
            apply_config_update(holder, {"llm.api_key": "sk-new-456"})
        assert "sk-secret-123" not in caplog.text
        assert "sk-new-456" not in caplog.text
        assert "<redacted>" in caplog.text
