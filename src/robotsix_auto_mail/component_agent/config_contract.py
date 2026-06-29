"""Config contract: snapshot, describe, validate, and apply for ``MailConfig``.

Implements the typed access patterns required by the ``config-get`` and
``config-set`` request kinds of the component-agent responder.  All
functions operate on the real ``MailConfig``, never on a parallel model.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from robotsix_auto_mail.config.schema import _FIELD_SPECS, _FieldSpec, _parse_bool

if TYPE_CHECKING:
    from robotsix_auto_mail.config.model import MailConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class ConfigContractError(Exception):
    """Raised on invalid config keys or values during ``config-set``.

    Mirrors the ``(code, message, **details)`` shape so it maps cleanly
    onto HTTP error responses.
    """

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


# ---------------------------------------------------------------------------
# Settable keys allowlist
# ---------------------------------------------------------------------------

# Runtime-safe dotted config keys ONLY.  Excludes startup-only fields:
# bind host/port, db/store paths, IMAP/SMTP host/credentials.
# Derived from ``_FIELD_SPECS[*].yaml_path`` so the allowlist references
# real paths.
_SETTABLE_YAML_PATHS: frozenset[str] = frozenset(
    {
        "ingest.interval_minutes",
        "triage.on_ingest",
        "calendar.enabled",
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
        "calendar_broker_token",
    }
)

_REDACTED = "<redacted>"

_yaml_path_to_spec: dict[str, _FieldSpec] = {s.yaml_path: s for s in _FIELD_SPECS}


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
    - Builds a candidate ``MailConfig`` via ``dataclasses.replace`` so
      ``__post_init__`` invariants run.
    - On any failure raises ``ConfigContractError(code="invalid_value")``.

    Returns an audit map ``{dotted_key: (old_value, new_value)}`` of the
    changes that **would** apply.
    """
    import dataclasses

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

    # -- Build candidate config via dataclasses.replace -------------------

    replace_kwargs: dict[str, object] = {}
    for key, new_val in coerced.items():
        spec = _yaml_path_to_spec[key]
        replace_kwargs[spec.field_name] = new_val

    try:
        dataclasses.replace(config, **replace_kwargs)  # type: ignore[arg-type]
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

    import dataclasses

    replace_kwargs: dict[str, object] = {}
    for key, (_, new_val) in audit.items():
        spec = _yaml_path_to_spec[key]
        replace_kwargs[spec.field_name] = new_val
    new_config = dataclasses.replace(old_config, **replace_kwargs)
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
    elif spec.kind in ("tls_mode", "log_level", "log_format", "calendar_transport"):
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
