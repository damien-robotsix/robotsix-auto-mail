"""Unit tests for the config schema module — helpers, error types, and field spec table."""

from __future__ import annotations

from pathlib import Path

import pytest

from robotsix_auto_mail.config.schema import (
    _FIELD_SPECS,
    _REQUIRED,
    ConfigurationError,
    _FieldSpec,
    _get_int,
    _get_str,
    _get_table,
    _mono_shape_error,
    _parse_bool,
)

# ---------------------------------------------------------------------------
# _mono_shape_error
# ---------------------------------------------------------------------------


def test_mono_shape_error_contains_path_and_commands() -> None:
    result = _mono_shape_error(Path("/etc/mail/my-config.yaml"))
    assert "/etc/mail/my-config.yaml" in result
    assert "detect" in result
    assert "single-account" in result
    assert "migrate-config" not in result


# ---------------------------------------------------------------------------
# ConfigurationError
# ---------------------------------------------------------------------------


def test_configuration_error_defaults() -> None:
    err = ConfigurationError("bad config")
    assert err.message == "bad config"
    assert err.missing_only is False
    assert str(err) == "bad config"


def test_configuration_error_missing_only_true() -> None:
    err = ConfigurationError("missing fields", missing_only=True)
    assert err.missing_only is True


def test_configuration_error_is_exception() -> None:
    with pytest.raises(ConfigurationError, match="test error"):
        raise ConfigurationError("test error missing")


# ---------------------------------------------------------------------------
# _parse_bool
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["1", "true", "yes", "on", "TRUE", "True", "YES", "ON", "TrUe"],
)
def test_parse_bool_truthy(raw: str) -> None:
    assert _parse_bool("test_label", raw) is True


@pytest.mark.parametrize(
    "raw",
    ["0", "false", "no", "off", "FALSE", "False", "NO", "OFF", "FaLsE"],
)
def test_parse_bool_falsy(raw: str) -> None:
    assert _parse_bool("test_label", raw) is False


def test_parse_bool_invalid() -> None:
    with pytest.raises(ConfigurationError, match="test_label must be a boolean"):
        _parse_bool("test_label", "maybe")


def test_parse_bool_empty_string() -> None:
    with pytest.raises(ConfigurationError):
        _parse_bool("label", "")


# ---------------------------------------------------------------------------
# _FieldSpec
# ---------------------------------------------------------------------------


def test_field_spec_construction() -> None:
    spec = _FieldSpec(
        field_name="imap_host",
        yaml_path="imap.host",
        kind="str",
        default=_REQUIRED,
        required_in_yaml=True,
    )
    assert spec.field_name == "imap_host"
    assert spec.yaml_path == "imap.host"
    assert spec.kind == "str"
    assert spec.default is _REQUIRED
    assert spec.required_in_yaml is True


# ---------------------------------------------------------------------------
# _FIELD_SPECS table self-consistency
# ---------------------------------------------------------------------------


def test_field_specs_field_names_unique() -> None:
    names = [s.field_name for s in _FIELD_SPECS]
    assert len(names) == len(set(names)), f"Duplicate field_names: {names}"


def test_field_specs_yaml_paths_unique() -> None:
    paths = [s.yaml_path for s in _FIELD_SPECS]
    assert len(paths) == len(set(paths)), f"Duplicate yaml_paths: {paths}"


def test_field_specs_yaml_paths_have_one_dot() -> None:
    for spec in _FIELD_SPECS:
        assert spec.yaml_path.count(".") == 1, f"Bad yaml_path: {spec.yaml_path!r}"


def test_field_specs_valid_kind_values() -> None:
    valid_kinds = {
        "str",
        "int",
        "bool",
        "tls_mode",
        "log_level",
        "log_format",
    }
    for spec in _FIELD_SPECS:
        assert spec.kind in valid_kinds, (
            f"Unknown kind {spec.kind!r} for {spec.field_name}"
        )


def test_field_specs_bool_kind_has_bool_default() -> None:
    for spec in _FIELD_SPECS:
        if spec.kind == "bool" and spec.default is not _REQUIRED:
            assert isinstance(spec.default, bool), (
                f"bool kind but non-bool default for {spec.field_name}: {spec.default!r}"
            )


def test_field_specs_int_kind_has_int_default() -> None:
    for spec in _FIELD_SPECS:
        if spec.kind == "int" and spec.default is not _REQUIRED:
            assert isinstance(spec.default, int), (
                f"int kind but non-int default for {spec.field_name}: {spec.default!r}"
            )


def test_field_specs_str_kind_has_str_default() -> None:
    for spec in _FIELD_SPECS:
        if spec.kind == "str" and spec.default is not _REQUIRED:
            assert isinstance(spec.default, str), (
                f"str kind but non-str default for {spec.field_name}: {spec.default!r}"
            )


def test_field_specs_required_fields_have_required_sentinel() -> None:
    for spec in _FIELD_SPECS:
        if spec.required_in_yaml:
            assert spec.default is _REQUIRED, (
                f"{spec.field_name} is required but default={spec.default!r}, "
                f"expected _REQUIRED sentinel"
            )


# ---------------------------------------------------------------------------
# _get_table
# ---------------------------------------------------------------------------


def test_get_table_present() -> None:
    result = _get_table({"imap": {"host": "x"}}, "imap")
    assert result == {"host": "x"}


def test_get_table_missing_key() -> None:
    assert _get_table({}, "imap") is None


def test_get_table_none_value() -> None:
    assert _get_table({"imap": None}, "imap") is None


def test_get_table_wrong_type() -> None:
    with pytest.raises(ConfigurationError, match="must be a table/mapping"):
        _get_table({"imap": "not a dict"}, "imap")


# ---------------------------------------------------------------------------
# _get_str
# ---------------------------------------------------------------------------


def test_get_str_present() -> None:
    assert _get_str({"host": "example.com"}, "host", "default") == "example.com"


def test_get_str_missing_key() -> None:
    assert _get_str({}, "host", "default") == "default"


def test_get_str_none_value() -> None:
    assert _get_str({"host": None}, "host", "default") == "default"


def test_get_str_wrong_type() -> None:
    with pytest.raises(ConfigurationError, match="must be a string"):
        _get_str({"host": 123}, "host", "default")


# ---------------------------------------------------------------------------
# _get_int
# ---------------------------------------------------------------------------


def test_get_int_present() -> None:
    assert _get_int({"port": 993}, "port", 0, Path("cfg.yaml")) == 993


def test_get_int_missing_key() -> None:
    assert _get_int({}, "port", 993, Path("cfg.yaml")) == 993


def test_get_int_none_value() -> None:
    assert _get_int({"port": None}, "port", 993, Path("cfg.yaml")) == 993


def test_get_int_wrong_type_str() -> None:
    with pytest.raises(ConfigurationError, match="must be an integer"):
        _get_int({"port": "993"}, "port", 0, Path("cfg.yaml"))


def test_get_int_wrong_type_bool() -> None:
    with pytest.raises(ConfigurationError, match="must be an integer, got bool"):
        _get_int({"port": True}, "port", 0, Path("cfg.yaml"))


# ---------------------------------------------------------------------------
# _REQUIRED sentinel
# ---------------------------------------------------------------------------


def test_required_sentinel_is_unique() -> None:
    assert _REQUIRED is not None
    assert isinstance(_REQUIRED, object)
    # _REQUIRED is Final, so it can't be rebound; just test identity.
    assert _REQUIRED is _REQUIRED
