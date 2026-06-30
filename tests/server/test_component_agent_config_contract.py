"""Unit tests for the config-contract module.

Covers ``get_config_snapshot``, ``describe_config``,
``validate_config_update``, ``apply_config_update``, and
``_coerce_value``.
"""

from __future__ import annotations

from typing import Any

import pytest

from robotsix_auto_mail.config.model import MailConfig
from robotsix_auto_mail.config.schema import _FIELD_SPECS, _FieldSpec
from robotsix_auto_mail.server._component_agent_config_contract import (
    SETTABLE_KEYS,
    ConfigContractError,
    _coerce_value,
    apply_config_update,
    describe_config,
    get_config_snapshot,
    validate_config_update,
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
    )
    kwargs.update(overrides)
    return MailConfig(**kwargs)  # type: ignore[arg-type]


class _ConfigHolder:
    """Minimal mutable holder for apply_config_update tests."""

    def __init__(self, config: MailConfig) -> None:
        self.config = config


# ---------------------------------------------------------------------------
# get_config_snapshot
# ---------------------------------------------------------------------------


class TestGetConfigSnapshot:
    def test_returns_flat_dotted_dict(self) -> None:
        cfg = _make_config()
        snap = get_config_snapshot(cfg)
        assert isinstance(snap, dict)
        assert "imap.host" in snap
        assert snap["imap.host"] == "imap.example.com"

    def test_includes_all_field_specs(self) -> None:
        cfg = _make_config()
        snap = get_config_snapshot(cfg)
        for spec in _FIELD_SPECS:
            assert spec.yaml_path in snap, f"Missing {spec.yaml_path}"

    def test_redacts_password(self) -> None:
        cfg = _make_config(password="secret123")
        snap = get_config_snapshot(cfg)
        assert snap["auth.password"] == "<redacted>"

    def test_redacts_llm_api_key(self) -> None:
        cfg = _make_config(llm_api_key="sk-abc123")
        snap = get_config_snapshot(cfg)
        assert snap["llm.api_key"] == "<redacted>"

    def test_redacts_langfuse_secret_key(self) -> None:
        cfg = _make_config(langfuse_secret_key="lf-secret")
        snap = get_config_snapshot(cfg)
        assert snap["langfuse.secret_key"] == "<redacted>"


# ---------------------------------------------------------------------------
# describe_config
# ---------------------------------------------------------------------------


class TestDescribeConfig:
    def test_returns_dict_with_metadata_per_key(self) -> None:
        cfg = _make_config()
        desc = describe_config(cfg)
        for spec in _FIELD_SPECS:
            assert spec.yaml_path in desc
            entry = desc[spec.yaml_path]
            assert "value" in entry
            assert "kind" in entry
            assert "settable" in entry

    def test_settable_flag_matches_settable_keys(self) -> None:
        cfg = _make_config()
        desc = describe_config(cfg)
        for key, entry in desc.items():
            assert entry["settable"] == (key in SETTABLE_KEYS)

    def test_redacts_password(self) -> None:
        cfg = _make_config(password="secret123")
        desc = describe_config(cfg)
        assert desc["auth.password"]["value"] == "<redacted>"

    def test_kind_matches_field_spec(self) -> None:
        cfg = _make_config()
        desc = describe_config(cfg)
        for spec in _FIELD_SPECS:
            assert desc[spec.yaml_path]["kind"] == spec.kind


# ---------------------------------------------------------------------------
# validate_config_update
# ---------------------------------------------------------------------------


class TestValidateConfigUpdate:
    # -- success paths -------------------------------------------------------

    def test_valid_int_update(self) -> None:
        cfg = _make_config(ingest_interval_minutes=5)
        audit = validate_config_update(cfg, {"ingest.interval_minutes": 10})
        assert "ingest.interval_minutes" in audit
        old, new = audit["ingest.interval_minutes"]
        assert old == 5
        assert new == 10

    def test_valid_str_update(self) -> None:
        cfg = _make_config(archive_root="/old")
        audit = validate_config_update(cfg, {"archive.root": "/new"})
        assert audit["archive.root"] == ("/old", "/new")

    def test_valid_bool_update(self) -> None:
        cfg = _make_config(triage_on_ingest=True)
        audit = validate_config_update(cfg, {"triage.on_ingest": False})
        assert audit["triage.on_ingest"] == (True, False)

    def test_multiple_keys_at_once(self) -> None:
        cfg = _make_config(
            triage_on_ingest=True, archive_enabled=False, ingest_interval_minutes=5
        )
        audit = validate_config_update(
            cfg,
            {
                "triage.on_ingest": False,
                "archive.enabled": True,
                "ingest.interval_minutes": 7,
            },
        )
        assert len(audit) == 3
        assert audit["triage.on_ingest"] == (True, False)
        assert audit["archive.enabled"] == (False, True)
        assert audit["ingest.interval_minutes"] == (5, 7)

    def test_does_not_mutate_config(self) -> None:
        cfg = _make_config(triage_on_ingest=True)
        validate_config_update(cfg, {"triage.on_ingest": False})
        assert cfg.triage_on_ingest is True

    # -- unknown key rejection -----------------------------------------------

    def test_rejects_unknown_key(self) -> None:
        cfg = _make_config()
        with pytest.raises(ConfigContractError) as exc_info:
            validate_config_update(cfg, {"nonexistent.key": "value"})
        assert exc_info.value.code == "invalid_key"
        assert exc_info.value.details.get("key") == "nonexistent.key"

    # -- non-settable key rejection -----------------------------------------

    def test_rejects_non_settable_key(self) -> None:
        cfg = _make_config()
        with pytest.raises(ConfigContractError) as exc_info:
            validate_config_update(cfg, {"imap.host": "evil.com"})
        assert exc_info.value.code == "invalid_key"

    # -- value coercion / validation ----------------------------------------

    def test_rejects_wrong_type_for_int(self) -> None:
        cfg = _make_config()
        with pytest.raises(ConfigContractError) as exc_info:
            validate_config_update(cfg, {"ingest.interval_minutes": "not_int"})
        assert exc_info.value.code == "invalid_value"

    def test_rejects_bool_for_int(self) -> None:
        cfg = _make_config()
        with pytest.raises(ConfigContractError) as exc_info:
            validate_config_update(cfg, {"ingest.interval_minutes": True})
        assert exc_info.value.code == "invalid_value"

    def test_rejects_wrong_type_for_str(self) -> None:
        cfg = _make_config()
        with pytest.raises(ConfigContractError) as exc_info:
            validate_config_update(cfg, {"archive.root": 123})
        assert exc_info.value.code == "invalid_value"

    def test_rejects_wrong_type_for_bool(self) -> None:
        cfg = _make_config()
        with pytest.raises(ConfigContractError) as exc_info:
            validate_config_update(cfg, {"triage.on_ingest": 123})
        assert exc_info.value.code == "invalid_value"

    # -- invariant violations via dataclasses.replace -----------------------

    def test_rejects_non_integer_for_int_field(self) -> None:
        """String value for an int field is rejected during coercion."""
        cfg = _make_config()
        with pytest.raises(ConfigContractError) as exc_info:
            validate_config_update(cfg, {"ingest.interval_minutes": "not_an_int"})
        assert exc_info.value.code == "invalid_value"

    # -- empty updates is a no-op -------------------------------------------

    def test_empty_updates_returns_empty_audit(self) -> None:
        cfg = _make_config()
        audit = validate_config_update(cfg, {})
        assert audit == {}


# ---------------------------------------------------------------------------
# apply_config_update
# ---------------------------------------------------------------------------


class TestApplyConfigUpdate:
    def test_applies_valid_update_and_mutates_holder(self) -> None:
        cfg = _make_config(triage_on_ingest=True)
        holder = _ConfigHolder(cfg)
        audit = apply_config_update(holder, {"triage.on_ingest": False})
        assert "triage.on_ingest" in audit
        assert holder.config.triage_on_ingest is False

    def test_secret_keys_redacted_in_log(self, caplog) -> None:
        import logging

        cfg = _make_config(llm_api_key="sk-abc123")
        holder = _ConfigHolder(cfg)
        with caplog.at_level(logging.INFO):
            apply_config_update(holder, {"llm.api_key": "sk-new"})
        # The log line should contain redacted values, not the real keys.
        assert "sk-abc123" not in caplog.text
        assert "sk-new" not in caplog.text

    def test_config_contract_error_propagates(self) -> None:
        cfg = _make_config()
        holder = _ConfigHolder(cfg)
        with pytest.raises(ConfigContractError):
            apply_config_update(holder, {"nonexistent.key": "value"})

    def test_holder_config_unchanged_on_error(self) -> None:
        cfg = _make_config(archive_root="/original")
        holder = _ConfigHolder(cfg)
        try:
            apply_config_update(holder, {"archive.root": 123})
        except ConfigContractError:
            pass
        assert holder.config.archive_root == "/original"


# ---------------------------------------------------------------------------
# _coerce_value
# ---------------------------------------------------------------------------


def _make_spec(
    field_name: str,
    yaml_path: str,
    kind: str,
    default: Any = "",
    env_key: str = "",
) -> _FieldSpec:
    return _FieldSpec(
        field_name=field_name,
        env_key=env_key,
        yaml_path=yaml_path,
        kind=kind,
        default=default,
        required_in_env=False,
        required_in_yaml=False,
    )


class TestCoerceValue:
    def test_str_accepts_string(self) -> None:
        spec = _make_spec("archive_root", "archive.root", "str")
        assert _coerce_value(spec, "/some/path") == "/some/path"

    def test_str_rejects_non_string(self) -> None:
        spec = _make_spec("archive_root", "archive.root", "str")
        with pytest.raises(ConfigContractError) as exc_info:
            _coerce_value(spec, 42)
        assert exc_info.value.code == "invalid_value"

    def test_int_accepts_integer(self) -> None:
        spec = _make_spec(
            "ingest_interval_minutes", "ingest.interval_minutes", "int", default=10
        )
        assert _coerce_value(spec, 42) == 42

    def test_int_rejects_bool(self) -> None:
        spec = _make_spec(
            "ingest_interval_minutes", "ingest.interval_minutes", "int", default=10
        )
        with pytest.raises(ConfigContractError) as exc_info:
            _coerce_value(spec, True)
        assert "bool" in exc_info.value.message

    def test_int_rejects_float(self) -> None:
        spec = _make_spec(
            "ingest_interval_minutes", "ingest.interval_minutes", "int", default=10
        )
        with pytest.raises(ConfigContractError) as exc_info:
            _coerce_value(spec, 3.14)
        assert exc_info.value.code == "invalid_value"

    def test_bool_accepts_true(self) -> None:
        spec = _make_spec("triage_on_ingest", "triage.on_ingest", "bool", default=True)
        assert _coerce_value(spec, True) is True

    def test_bool_accepts_false(self) -> None:
        spec = _make_spec("triage_on_ingest", "triage.on_ingest", "bool", default=True)
        assert _coerce_value(spec, False) is False

    @pytest.mark.parametrize("raw", ["true", "True", "TRUE", "1", "yes", "on"])
    def test_bool_parses_string_true(self, raw: str) -> None:
        spec = _make_spec("triage_on_ingest", "triage.on_ingest", "bool", default=True)
        assert _coerce_value(spec, raw) is True

    @pytest.mark.parametrize("raw", ["false", "False", "FALSE", "0", "no", "off"])
    def test_bool_parses_string_false(self, raw: str) -> None:
        spec = _make_spec("triage_on_ingest", "triage.on_ingest", "bool", default=True)
        assert _coerce_value(spec, raw) is False

    def test_bool_rejects_invalid_string(self) -> None:
        spec = _make_spec("triage_on_ingest", "triage.on_ingest", "bool", default=True)
        with pytest.raises(ConfigContractError) as exc_info:
            _coerce_value(spec, "maybe")
        assert exc_info.value.code == "invalid_value"

    def test_bool_rejects_int(self) -> None:
        spec = _make_spec("triage_on_ingest", "triage.on_ingest", "bool", default=True)
        with pytest.raises(ConfigContractError) as exc_info:
            _coerce_value(spec, 1)
        assert exc_info.value.code == "invalid_value"

    def test_tls_mode_accepts_string(self) -> None:
        spec = _make_spec("imap_tls_mode", "imap.tls_mode", "tls_mode", default="SSL")
        assert _coerce_value(spec, "STARTTLS") == "STARTTLS"

    def test_tls_mode_rejects_non_string(self) -> None:
        spec = _make_spec("imap_tls_mode", "imap.tls_mode", "tls_mode", default="SSL")
        with pytest.raises(ConfigContractError) as exc_info:
            _coerce_value(spec, 42)
        assert exc_info.value.code == "invalid_value"

    def test_log_level_accepts_string(self) -> None:
        spec = _make_spec("log_level", "logging.level", "log_level", default="INFO")
        assert _coerce_value(spec, "DEBUG") == "DEBUG"

    def test_log_format_accepts_string(self) -> None:
        spec = _make_spec(
            "log_format", "logging.format", "log_format", default="console"
        )
        assert _coerce_value(spec, "json") == "json"

    def test_unknown_kind_raises(self) -> None:
        spec = _make_spec("some_field", "some.path", "fantasy_type")
        with pytest.raises(ConfigContractError) as exc_info:
            _coerce_value(spec, "anything")
        assert exc_info.value.code == "invalid_value"
        assert "fantasy_type" in exc_info.value.message


# ---------------------------------------------------------------------------
# ConfigContractError
# ---------------------------------------------------------------------------


class TestConfigContractError:
    def test_stores_code_message_and_details(self) -> None:
        exc = ConfigContractError(
            code="invalid_key", message="bad key", key="foo.bar", extra=42
        )
        assert exc.code == "invalid_key"
        assert exc.message == "bad key"
        assert exc.details == {"key": "foo.bar", "extra": 42}

    def test_is_exception_subclass(self) -> None:
        exc = ConfigContractError(code="x", message="y")
        assert isinstance(exc, Exception)

    def test_str_returns_message(self) -> None:
        exc = ConfigContractError(code="invalid_value", message="bad value")
        assert str(exc) == "bad value"
