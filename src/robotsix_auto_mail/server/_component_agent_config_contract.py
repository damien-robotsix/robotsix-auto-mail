"""Config contract: snapshot, describe, validate, and apply for ``MailConfig``.

Implements the typed access patterns required by the ``config-get`` and
``config-set`` request kinds of the component-agent responder.  All
functions operate on the real ``MailConfig``, never on a parallel model.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from robotsix_agent_comm.protocol import ConfigContractError

from robotsix_auto_mail.config.model import MailConfig

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

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

# Mapping: dotted YAML key → MailConfig field name
_YAML_PATH_TO_FIELD: dict[str, str] = {
    "imap.host": "imap_host",
    "imap.port": "imap_port",
    "imap.tls_mode": "imap_tls_mode",
    "imap.folder": "imap_folder",
    "smtp.host": "smtp_host",
    "smtp.port": "smtp_port",
    "smtp.tls_mode": "smtp_tls_mode",
    "auth.username": "username",
    "auth.password": "password",
    "auth.oauth2_provider": "oauth2_provider",
    "auth.oauth2_tenant": "oauth2_tenant",
    "auth.oauth2_token": "oauth2_token",
    "auth.oauth2_client_id": "oauth2_client_id",
    "auth.oauth2_client_secret": "oauth2_client_secret",
    "store.path": "db_path",
    "archive.root": "archive_root",
    "archive.enabled": "archive_enabled",
    "triage.on_ingest": "triage_on_ingest",
    "triage.rules_path": "triage_rules_path",
    "ingest.interval_minutes": "ingest_interval_minutes",
    "component_agent.enabled": "component_agent_enabled",
    "llm.api_key": "llm_api_key",
    "llm.provider_model": "llm_provider_model",
    "langfuse.public_key": "langfuse_public_key",
    "langfuse.secret_key": "langfuse_secret_key",
    "langfuse.base_url": "langfuse_base_url",
    "logging.level": "log_level",
    "logging.format": "log_format",
    "logging.file_dir": "log_file_dir",
}

# Reverse mapping for emitting snapshots: field_name → dotted path
_FIELD_TO_YAML_PATH: dict[str, str] = {v: k for k, v in _YAML_PATH_TO_FIELD.items()}


def _field_value(config: MailConfig, field_name: str) -> object:
    """Return the current value of *field_name* from *config*, redacted if secret."""
    raw = getattr(config, field_name)
    if field_name in _SECRET_FIELDS:
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
    for field_name in MailConfig.model_fields:
        dotted = _FIELD_TO_YAML_PATH.get(field_name, field_name)
        snapshot[dotted] = _field_value(config, field_name)
    return snapshot


def describe_config(config: MailConfig) -> dict[str, Any]:
    """Return metadata for each settable key: current (redacted) value,
    type/kind, and whether it is settable.
    """
    result: dict[str, Any] = {}
    for field_name, field_info in MailConfig.model_fields.items():
        dotted = _FIELD_TO_YAML_PATH.get(field_name, field_name)
        annotation = field_info.annotation
        kind = "str"
        if annotation is bool:
            kind = "bool"
        elif annotation is int:
            kind = "int"
        result[dotted] = {
            "value": _field_value(config, field_name),
            "kind": kind,
            "settable": dotted in SETTABLE_KEYS,
        }
    return result


def validate_config_update(
    config: MailConfig, updates: dict[str, Any]
) -> dict[str, tuple[object, object]]:
    """Validate an update map against *config* without mutating.

    - Rejects unknown or non-settable keys with
      ``ConfigContractError(code="invalid_key")``.
    - Coerces/validates each value through a candidate ``MailConfig``.
    - Builds a candidate ``MailConfig`` via ``model_copy`` so
      pydantic validators run.
    - On any failure raises ``ConfigContractError(code="invalid_value")``.

    Returns an audit map ``{dotted_key: (old_value, new_value)}`` of the
    changes that **would** apply.
    """

    # -- Reject unknown / non-settable keys --------------------------------

    for key in updates:
        if key not in _YAML_PATH_TO_FIELD:
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

    # -- Build field-level update dict ------------------------------------

    replace_kwargs: dict[str, object] = {}
    for key, raw_value in updates.items():
        field_name = _YAML_PATH_TO_FIELD[key]
        replace_kwargs[field_name] = raw_value

    # -- Build candidate config via model_copy ----------------------------

    try:
        config.model_copy(update=replace_kwargs)
    except ValidationError as exc:
        raise ConfigContractError(
            code="invalid_value",
            message=str(exc),
        ) from exc
    except Exception as exc:
        raise ConfigContractError(
            code="invalid_value",
            message=f"Invalid value for config update: {exc}",
        ) from exc

    # -- Build audit map --------------------------------------------------

    audit: dict[str, tuple[object, object]] = {}
    for key, new_val in replace_kwargs.items():
        old_val = getattr(config, key)
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
        replace_kwargs[key] = new_val
    new_config = old_config.model_copy(update=replace_kwargs)
    holder.config = new_config

    # Emit audit log with secrets redacted.
    redacted: dict[str, tuple[object, object]] = {}
    for key, (old, new) in audit.items():
        if key in _SECRET_FIELDS:
            redacted[key] = (_REDACTED, _REDACTED)
        else:
            redacted[key] = (old, new)
    logger.info("config-set applied: %s", redacted)

    return audit
