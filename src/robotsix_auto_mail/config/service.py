"""The standard component config surface, as pure functions.

Implements the read/write semantics robotsix-standards ``config-ownership.md``
requires of every deployable component, over the one ``config.json`` this
component already loads:

- secrets are **typed** — taken from ``SecretStr`` fields on the model, never
  guessed from a field's name — masked on read and merged on write;
- updates are partial: an omitted key keeps its stored value;
- every write is versioned, and a version can be rolled back to.

The HTTP layer in :mod:`robotsix_auto_mail.server` is a thin shell over this
module.
"""

from __future__ import annotations

import json
import logging
import types
import typing
from typing import Any

from pydantic import BaseModel, SecretStr, ValidationError

from robotsix_auto_mail.config.loader import (
    get_config_schema,
    load_accounts,
    save_accounts,
)
from robotsix_auto_mail.config.model import MailAccountsConfig
from robotsix_auto_mail.config.versions import VERSIONS_FILENAME, ConfigVersionStore

logger = logging.getLogger(__name__)

#: What pydantic renders a non-empty ``SecretStr`` as in JSON mode.  The panel
#: shows a "set" badge for it and sends it back unchanged when untouched.
MASK = "**********"

#: Item keys tried, in order, when matching list entries across an update.
#: Matching by identity rather than position keeps a secret attached to its
#: own account when the operator reorders or removes one.
_IDENTITY_KEYS = ("account_id", "id", "name")


class ConfigValidationError(Exception):
    """A rejected update.  *key* is the dotted path the message blames."""

    def __init__(self, detail: str, key: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.key = key


# ---------------------------------------------------------------------------
# Model introspection
# ---------------------------------------------------------------------------


def _unwrap_optional(annotation: Any) -> Any:
    """Return the single non-``None`` branch of an optional annotation."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        branches = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(branches) == 1:
            return branches[0]
    return annotation


def _classify(annotation: Any) -> tuple[str, type[BaseModel] | None]:
    """Classify a field annotation as ``secret`` / ``model`` / ``list`` / ``scalar``."""
    annotation = _unwrap_optional(annotation)
    if annotation is SecretStr:
        return "secret", None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return "model", annotation
    if typing.get_origin(annotation) is list:
        args = typing.get_args(annotation)
        if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
            return "list", args[0]
    return "scalar", None


def _identity_key(model_cls: type[BaseModel]) -> str | None:
    """The field used to match list entries across an update, if any."""
    for candidate in _IDENTITY_KEYS:
        if candidate in model_cls.model_fields:
            return candidate
    return None


# ---------------------------------------------------------------------------
# Secret handling
# ---------------------------------------------------------------------------


def strip_secrets(model_cls: type[BaseModel], data: dict[str, Any]) -> dict[str, Any]:
    """Return *data* without any value belonging to a ``SecretStr`` field.

    Used before writing version history, which must never store a secret.
    """
    out: dict[str, Any] = {}
    for key, value in data.items():
        info = model_cls.model_fields.get(key)
        if info is None:
            out[key] = value
            continue
        kind, sub = _classify(info.annotation)
        if kind == "secret":
            continue
        if kind == "model" and sub is not None and isinstance(value, dict):
            out[key] = strip_secrets(sub, value)
        elif kind == "list" and sub is not None and isinstance(value, list):
            out[key] = [
                strip_secrets(sub, item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            out[key] = value
    return out


def merge_updates(
    model_cls: type[BaseModel],
    current: dict[str, Any],
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Apply the partial *updates* onto *current*.

    Keys absent from *updates* keep their stored value.  A secret submitted
    blank or as the mask sentinel is treated as "unchanged" — only an
    explicitly typed, non-blank secret overwrites the stored one.
    """
    merged = dict(current)
    for key, value in updates.items():
        info = model_cls.model_fields.get(key)
        if info is None:
            merged[key] = value
            continue
        kind, sub = _classify(info.annotation)

        if kind == "secret":
            if isinstance(value, str) and value in ("", MASK):
                continue
            merged[key] = value
        elif kind == "model" and sub is not None and isinstance(value, dict):
            existing = current.get(key)
            merged[key] = merge_updates(
                sub, existing if isinstance(existing, dict) else {}, value
            )
        elif kind == "list" and sub is not None and isinstance(value, list):
            existing_list = current.get(key)
            merged[key] = _merge_list(
                sub, existing_list if isinstance(existing_list, list) else [], value
            )
        else:
            merged[key] = value
    return merged


def _merge_list(
    model_cls: type[BaseModel],
    current: list[Any],
    updates: list[Any],
) -> list[Any]:
    """Merge a list of models, matching entries by identity then by position."""
    identity = _identity_key(model_cls)
    by_identity: dict[Any, dict[str, Any]] = {}
    if identity:
        for item in current:
            if isinstance(item, dict) and item.get(identity) is not None:
                by_identity[item[identity]] = item

    merged: list[Any] = []
    for index, item in enumerate(updates):
        if not isinstance(item, dict):
            merged.append(item)
            continue
        existing: dict[str, Any] | None = None
        if identity and item.get(identity) is not None:
            existing = by_identity.get(item[identity])
        if existing is None and index < len(current):
            positional = current[index]
            # Only fall back to position when the entry is not identified —
            # otherwise a reorder would graft one account's secret onto another.
            if isinstance(positional, dict) and (
                not identity or positional.get(identity) == item.get(identity)
            ):
                existing = positional
        merged.append(merge_updates(model_cls, existing or {}, item))
    return merged


# ---------------------------------------------------------------------------
# The surface
# ---------------------------------------------------------------------------


def _version_store() -> ConfigVersionStore:
    from robotsix_config import resolve_config_path

    return ConfigVersionStore(resolve_config_path().parent / VERSIONS_FILENAME)


def _load() -> MailAccountsConfig:
    """The stored config, or an empty one when the file is missing/invalid."""
    try:
        return load_accounts()
    except Exception:
        logger.warning("Config file unreadable; serving an empty config", exc_info=True)
        return MailAccountsConfig(accounts=[], default_account_id="")


def masked_config() -> dict[str, Any]:
    """The effective config with every ``SecretStr`` rendered as its mask."""
    return _load().model_dump(mode="json")


def _schema() -> dict[str, Any]:
    schema: dict[str, Any] = json.loads(get_config_schema())
    return schema


def get_config() -> dict[str, Any]:
    """``GET /config`` — effective values, the typed schema, and the version."""
    store = _version_store()
    config = masked_config()
    version = store.current_version()
    if version == 0:
        # Seed the history so the first read already has something to roll
        # back to and the version number is meaningful.
        version = store.record(strip_secrets(MailAccountsConfig, config), ["(initial)"])
    return {"config": config, "schema": _schema(), "version": version}


def changed_keys(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Dotted paths whose value differs, with secret leaves marked."""
    changed: list[str] = []
    _collect_changes(MailAccountsConfig, before, after, "", changed)
    return changed


def _collect_changes(
    model_cls: type[BaseModel] | None,
    before: Any,
    after: Any,
    prefix: str,
    out: list[str],
) -> None:
    if not isinstance(before, dict) or not isinstance(after, dict):
        if before != after and prefix:
            out.append(prefix)
        return
    for key in sorted(set(before) | set(after)):
        path = f"{prefix}.{key}" if prefix else key
        info = model_cls.model_fields.get(key) if model_cls else None
        kind, sub = _classify(info.annotation) if info else ("scalar", None)
        if kind == "secret":
            if before.get(key) != after.get(key):
                out.append(f"{path} (secret)")
        elif kind == "model":
            _collect_changes(sub, before.get(key), after.get(key), path, out)
        elif kind == "list":
            before_items = before.get(key) or []
            after_items = after.get(key) or []
            if len(before_items) != len(after_items):
                out.append(path)
                continue
            for index, (was, now) in enumerate(
                zip(before_items, after_items, strict=True)
            ):
                _collect_changes(sub, was, now, f"{path}.{index}", out)
        elif before.get(key) != after.get(key):
            out.append(path)


def _validate_or_raise(candidate: dict[str, Any]) -> MailAccountsConfig:
    try:
        return MailAccountsConfig.model_validate(candidate)
    except ValidationError as exc:
        first = exc.errors()[0]
        key = ".".join(str(part) for part in first.get("loc", ()))
        detail = f"{key}: {first.get('msg')}" if key else str(first.get("msg"))
        raise ConfigValidationError(detail, key or None) from exc
    except Exception as exc:  # ConfigurationError from the model validators
        raise ConfigValidationError(str(exc)) from exc


def _apply(candidate: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    """Validate, persist, and version *candidate*.  Returns the write response.

    *before* carries the real secret values, not their mask — otherwise every
    secret would compare equal to itself and a rotation would go unrecorded.
    Only the resulting *paths* are stored; no value ever is.
    """
    validated = _validate_or_raise(candidate)
    store = _version_store()
    if store.current_version() == 0:
        # Record the pre-write state first, so the very first change is still
        # something the operator can roll back out of.
        store.record(strip_secrets(MailAccountsConfig, before), ["(initial)"])
    save_accounts(validated)
    masked_after = validated.model_dump(mode="json")
    after = _with_real_secrets(MailAccountsConfig, validated, masked_after)
    version = store.record(
        strip_secrets(MailAccountsConfig, masked_after), changed_keys(before, after)
    )
    return {"config": masked_after, "version": version}


def update_config(updates: dict[str, Any]) -> dict[str, Any]:
    """``PUT /config`` — apply a partial update.

    Raises:
        ConfigValidationError: When the merged result fails validation.  The
            stored config is left untouched.
    """
    stored = _load()
    masked = stored.model_dump(mode="json")
    # model_dump masks secrets, so re-attach the real values before merging —
    # otherwise saving would persist "**********" as the password.
    current = _with_real_secrets(MailAccountsConfig, stored, masked)
    return _apply(merge_updates(MailAccountsConfig, current, updates), current)


def rollback(version: int) -> dict[str, Any]:
    """``POST /config/rollback`` — restore *version* as a new version.

    Version history holds no secrets, so the current secrets are preserved
    across the rollback rather than being wiped.
    """
    snapshot = _version_store().snapshot(version)
    if snapshot is None:
        raise ConfigValidationError(f"unknown config version: {version}", None)
    stored = _load()
    masked = stored.model_dump(mode="json")
    current = _with_real_secrets(MailAccountsConfig, stored, masked)
    return _apply(merge_updates(MailAccountsConfig, current, snapshot), current)


def list_versions() -> dict[str, Any]:
    """``GET /config/versions`` — recent versions, newest first."""
    store = _version_store()
    if store.current_version() == 0:
        get_config()  # seed the initial version
    return {"versions": store.entries()}


def _with_real_secrets(
    model_cls: type[BaseModel], model: BaseModel, dumped: dict[str, Any]
) -> dict[str, Any]:
    """Replace masked secrets in *dumped* with their real values from *model*."""
    out = dict(dumped)
    for key, info in model_cls.model_fields.items():
        if key not in out:
            continue
        kind, sub = _classify(info.annotation)
        value = getattr(model, key, None)
        if kind == "secret" and isinstance(value, SecretStr):
            out[key] = value.get_secret_value()
        elif (
            kind == "model"
            and sub is not None
            and isinstance(value, BaseModel)
            and isinstance(out[key], dict)
        ):
            out[key] = _with_real_secrets(sub, value, out[key])
        elif (
            kind == "list"
            and sub is not None
            and isinstance(value, list)
            and isinstance(out[key], list)
        ):
            out[key] = [
                _with_real_secrets(sub, item, dumped_item)
                if isinstance(item, BaseModel) and isinstance(dumped_item, dict)
                else dumped_item
                for item, dumped_item in zip(value, out[key], strict=True)
            ]
    return out
