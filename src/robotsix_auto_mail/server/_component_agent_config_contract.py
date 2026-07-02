"""Config contract: snapshot, describe, validate, and apply for ``MailConfig``.

Implements the typed access patterns required by the ``config-get`` and
``config-set`` request kinds of the component-agent responder.  All
functions operate on the real ``MailConfig``, never on a parallel model.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NamedTuple

from robotsix_agent_comm.protocol import ConfigContractError

from robotsix_auto_mail.config.model import MailConfig

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Field spec — replaces the deleted robotsix_auto_mail.config.schema._FieldSpec
# ---------------------------------------------------------------------------


class _FieldSpec(NamedTuple):
    """How to describe one ``MailConfig`` field in the config contract."""

    field_name: str
    yaml_path: str  # kept as "yaml_path" for backward compat with callers
    kind: str
    default: Any
    required_in_yaml: bool = False


# ---------------------------------------------------------------------------
# Build field specs from the pydantic model
# ---------------------------------------------------------------------------

# Map of pydantic field name → dotted YAML-style path (preserved for API compat).
_FIELD_YAML_MAP: dict[str, str] = {
    "imap_host": "imap.host",
    "imap_port": "imap.port",
    "imap_tls_mode": "imap.tls_mode",
    "imap_folder": "imap.folder",
    "smtp_host": "smtp.host",
    "smtp_port": "smtp.port",
    "smtp_tls_mode": "smtp.tls_mode",
    "username": "auth.username",
    "password": "auth.password",  # pragma: allowlist secret
    "oauth2_token": "auth.oauth2_token",  # pragma: allowlist secret
    "oauth2_client_id": "auth.oauth2_client_id",
    "oauth2_client_secret": "auth.oauth2_client_secret",  # pragma: allowlist secret
    "oauth2_provider": "auth.oauth2_provider",
    "oauth2_tenant": "auth.oauth2_tenant",
    "db_path": "store.path",
    "llm_api_key": "llm.api_key",
    "llm_provider_model": "llm.provider_model",
    "ingest_interval_minutes": "ingest.interval_minutes",
    "archive_root": "archive.root",
    "archive_enabled": "archive.enabled",
    "triage_on_ingest": "triage.on_ingest",
    "triage_rules_path": "triage.rules_path",
    "component_agent_enabled": "component_agent.enabled",
    "langfuse_public_key": "langfuse.public_key",
    "langfuse_secret_key": "langfuse.secret_key",  # pragma: allowlist secret
    "langfuse_base_url": "langfuse.base_url",
    "log_level": "logging.level",
    "log_format": "logging.format",
    "log_file_dir": "logging.file_dir",
}


def _kind_for_field(field_name: str) -> str:
    """Determine the 'kind' tag for a field based on its Python type."""
    annotation = MailConfig.model_fields[field_name].annotation
    if annotation is int:
        return "int"
    elif annotation is bool:
        return "bool"
    elif field_name in ("imap_tls_mode", "smtp_tls_mode"):
        return "tls_mode"
    elif field_name == "log_level":
        return "log_level"
    elif field_name == "log_format":
        return "log_format"
    return "str"


# Derive field specs from the pydantic model fields
_FIELD_SPECS: tuple[_FieldSpec, ...] = tuple(
    _FieldSpec(
        field_name=name,
        yaml_path=_FIELD_YAML_MAP[name],
        kind=_kind_for_field(name),
        default=field_info.default if field_info.default is not ... else "",
        required_in_yaml=field_info.is_required(),
    )
    for name, field_info in MailConfig.model_fields.items()
    if name in _FIELD_YAML_MAP
)


# ---------------------------------------------------------------------------
# Settable keys allowlist
# ---------------------------------------------------------------------------

# Runtime-safe dotted config keys ONLY.  Excludes startup-only fields:
# bind host/port, db/store paths, IMAP/SMTP host/credentials.
_SETTABLE_YAML_PATHS: frozenset[str] = frozenset(
    {
        "ingest.interval_minutes",
        "triage.on_ingest",
        "archive.enabled",
        "archive.root",
        "logging.level",
        "logging.format",
        "logging.file_dir",
        "llm.api_key",
        "langfuse.public_key",
        "langfuse.secret_key",
        "langfuse.base_url",
    }
)

# Validate that every settable path references a real _FieldSpec.
_all_yaml_paths = {s.yaml_path for s in _FIELD_SPECS}
_unknown_settable = _SETTABLE_YAML_PATHS - _all_yaml_paths
if _unknown_settable:  # pragma: no cover
    raise AssertionError(
        f"SETTABLE_KEYS references unknown yaml_path(s): {_unknown_settable!r}"
    )

# Public frozenset of dotted keys.
SETTABLE_KEYS: frozenset[str] = _SETTABLE_YAML_PATHS

# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

# fields that contain secrets — redacted in snapshots.
_SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "password",
        "llm_api_key",
        "oauth2_token",
        "oauth2_client_secret",
        "langfuse_secret_key",
    }
)

_REDACTED = "<redacted>"

_yaml_path_to_spec: dict[str, _FieldSpec] = {s.yaml_path: s for s in _FIELD_SPECS}

# Boolean parsing (was in schema.py)
_BOOL_TRUE = frozenset({"1", "true", "yes", "on"})
_BOOL_FALSE = frozenset({"0", "false", "no", "off"})


def _parse_bool(label: str, raw: str) -> bool:
    lowered = raw.lower()
    if lowered in _BOOL_TRUE:
        return True
    if lowered in _BOOL_FALSE:
        return False
    raise ValueError(f"{label} must be a boolean, got {raw!r}")


def _field_value(config: MailConfig, spec: _FieldSpec) -> object:
    """Return the current value of *spec* from *config*, redacted if secret."""
    raw = getattr(config, spec.field_name)
    if spec.field_name in _SECRET_FIELDS:
        return _REDACTED
    return raw


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_config_snapshot(config: MailConfig) -> dict[str, Any]:
    """Return a flat dotted-path map of every ``MailConfig`` field.

    Every secret field (``_SECRET_FIELDS``) value is replaced by
    ``"<redacted>"``; raw secret values are **never** emitted.
    """
    snapshot: dict[str, Any] = {}
    for spec in _FIELD_SPECS:
        snapshot[spec.yaml_path] = _field_value(config, spec)
    return snapshot


def describe_config(config: MailConfig) -> dict[str, Any]:
    """Return metadata for each settable key: current (redacted) value,
    type/kind, and whether it is settable.
    """
    result: dict[str, Any] = {}
    for spec in _FIELD_SPECS:
        dotted = spec.yaml_path
        result[dotted] = {
            "value": _field_value(config, spec),
            "kind": spec.kind,
            "settable": dotted in SETTABLE_KEYS,
        }
    return result


def validate_config_update(
    config: MailConfig, updates: dict[str, Any]
) -> dict[str, tuple[object, object]]:
    """Validate an update map against *config* without mutating.

    - Rejects unknown or non-settable keys with
      ``ConfigContractError(code="invalid_key")``.
    - Coerces/validates each value per the field's ``kind``.
    - Builds a candidate ``MailConfig`` via ``model_copy`` so
      pydantic invariants run.
    - On any failure raises ``ConfigContractError(code="invalid_value")``.

    Returns an audit map ``{dotted_key: (old_value, new_value)}`` of the
    changes that **would** apply.
    """
    # -- Reject unknown / non-settable keys --------------------------------

    for key in updates:
        if key not in _yaml_path_to_spec:
            raise ConfigContractError(
                code="invalid_key",
                message=f"Unknown config key: {key!r}",
                key=key,
            )
        if key not in SETTABLE_KEYS:
            raise ConfigContractError(
                code="invalid_key",
                message=f"Config key {key!r} is not settable at runtime",
                key=key,
            )

    # -- Coerce + validate each value -------------------------------------

    coerced: dict[str, object] = {}
    for key, raw_value in updates.items():
        spec = _yaml_path_to_spec[key]
        try:
            coerced[key] = _coerce_value(spec, raw_value)
        except ConfigContractError:
            raise
        except Exception as exc:
            raise ConfigContractError(
                code="invalid_value",
                message=f"Invalid value for {key}: {exc}",
                key=key,
            ) from exc

    # -- Build candidate config via model_copy ---------------------------

    replace_kwargs: dict[str, object] = {}
    for key, new_val in coerced.items():
        spec = _yaml_path_to_spec[key]
        replace_kwargs[spec.field_name] = new_val

    try:
        config.model_copy(update=replace_kwargs)
    except Exception as exc:
        raise ConfigContractError(
            code="invalid_value",
            message=str(exc),
        ) from exc

    # -- Build audit map --------------------------------------------------

    audit: dict[str, tuple[object, object]] = {}
    for key, new_val in coerced.items():
        spec = _yaml_path_to_spec[key]
        old_val = getattr(config, spec.field_name)
        audit[key] = (old_val, new_val)

    return audit


def apply_config_update(
    holder: Any,  # object with a mutable ``config`` attribute
    updates: dict[str, Any],
) -> dict[str, tuple[object, object]]:
    """Validate *updates* against ``holder.config``, then apply atomically.

    After successful validation, swaps ``holder.config`` to the validated
    candidate ``MailConfig`` and emits an audit log line with secret values
    redacted.

    Returns the audit map ``{dotted_key: (old_value, new_value)}``.
    """
    old_config = holder.config
    audit = validate_config_update(old_config, updates)

    replace_kwargs: dict[str, object] = {}
    for key, (_, new_val) in audit.items():
        spec = _yaml_path_to_spec[key]
        replace_kwargs[spec.field_name] = new_val
    new_config = old_config.model_copy(update=replace_kwargs)
    holder.config = new_config

    # Emit audit log with secrets redacted.
    redacted: dict[str, tuple[object, object]] = {}
    for key, (old, new) in audit.items():
        spec = _yaml_path_to_spec[key]
        if spec.field_name in _SECRET_FIELDS:
            redacted[key] = (_REDACTED, _REDACTED)
        else:
            redacted[key] = (old, new)
    logger.info("config-set applied: %s", redacted)

    return audit


# ---------------------------------------------------------------------------
# Internal: value coercion
# ---------------------------------------------------------------------------


def _coerce_value(spec: _FieldSpec, raw: object) -> object:
    """Coerce *raw* to the type declared by *spec.kind*.

    Raises ``ConfigContractError`` on type mismatch or invalid value.
    """
    if spec.kind == "str":
        if not isinstance(raw, str):
            raise ConfigContractError(
                code="invalid_value",
                message=f"{spec.yaml_path} must be a string, got {type(raw).__name__}",
                key=spec.yaml_path,
            )
        return raw
    elif spec.kind == "int":
        if isinstance(raw, bool):
            raise ConfigContractError(
                code="invalid_value",
                message=f"{spec.yaml_path} must be an integer, got bool",
                key=spec.yaml_path,
            )
        if not isinstance(raw, int):
            raise ConfigContractError(
                code="invalid_value",
                message=(
                    f"{spec.yaml_path} must be an integer, got {type(raw).__name__}"
                ),
                key=spec.yaml_path,
            )
        return raw
    elif spec.kind == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            try:
                return _parse_bool(spec.yaml_path, raw)
            except Exception as exc:
                raise ConfigContractError(
                    code="invalid_value",
                    message=str(exc),
                    key=spec.yaml_path,
                ) from exc
        raise ConfigContractError(
            code="invalid_value",
            message=f"{spec.yaml_path} must be a boolean, got {type(raw).__name__}",
            key=spec.yaml_path,
        )
    elif spec.kind in ("tls_mode", "log_level", "log_format"):
        # These kinds are not in SETTABLE_KEYS, but handle gracefully.
        if not isinstance(raw, str):
            raise ConfigContractError(
                code="invalid_value",
                message=f"{spec.yaml_path} must be a string, got {type(raw).__name__}",
                key=spec.yaml_path,
            )
        return raw
    else:
        # Unknown kind — should not happen with validated _FIELD_SPECS.
        raise ConfigContractError(
            code="invalid_value",
            message=f"Unknown field kind {spec.kind!r} for {spec.yaml_path}",
            key=spec.yaml_path,
        )
