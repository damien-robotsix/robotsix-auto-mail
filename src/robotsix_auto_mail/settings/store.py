"""``SettingsStore`` — per-account key-value settings backed by SQLite.

Secrets (fields whose name ends with ``_key``, ``_secret``, ``password``,
or ``_token``) are stored in plain text inside the DB (the DB file lives
on the component's own encrypted volume) but are **masked** as ``"***"``
in every read path — both the Python API and the JSON response from
GET /settings.
"""

from __future__ import annotations

from pydantic import SecretStr

from robotsix_auto_mail.config.model import MailConfig
from robotsix_auto_mail.db import (
    component_settings_count,
    get_all_component_settings,
    set_component_settings,
)


#: Field-name suffixes and exact matches that mark a value as secret.
#: Values for these fields are always masked as ``"***"`` on read.
_SECRET_FIELD_SUFFIXES: tuple[str, ...] = (
    "_key",
    "_secret",
    "password",
    "_token",
    "oauth2_token",
)
#: Exact field names that are secret but don't match a suffix above.
_SECRET_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "oauth2_token",
        "oauth2_client_secret",
    }
)


def _is_secret_field(field_name: str) -> bool:
    """Return ``True`` if *field_name* identifies a secret that must be masked."""
    if field_name in _SECRET_FIELD_NAMES:
        return True
    return any(field_name.endswith(suffix) for suffix in _SECRET_FIELD_SUFFIXES)


def _masked_value(field_name: str, value: str) -> str:
    """Return ``"***"`` for secret fields, otherwise the original *value*."""
    return "***" if _is_secret_field(field_name) else value


def _validate_field(field_name: str, value: str) -> str | None:
    """Validate *value* against ``MailConfig`` field constraints.

    Returns an error message string on failure or ``None`` on success.
    Only validates fields that exist on ``MailConfig`` and have
    ``field_validator`` or type constraints; unknown fields are
    rejected outright.
    """
    field_info = MailConfig.model_fields.get(field_name)
    if field_info is None:
        return f"unknown setting: {field_name!r}"

    annotation = field_info.annotation

    # Handle SecretStr — accept plain strings, store as-is.
    if annotation is SecretStr:
        # Allow any non-empty string for SecretStr fields.
        if not isinstance(value, str):
            return f"{field_name!r}: expected a string value"
        return None

    # Coerce through Pydantic's field validator by constructing a
    # temporary MailConfig.  We only validate the one field at a time
    # to avoid coupling to unrelated fields.
    try:
        # Build a minimal valid config and patch the target field.
        defaults: dict[str, object] = {}
        for name, info in MailConfig.model_fields.items():
            if info.default is not ... and info.default is not None:
                defaults[name] = info.default
            elif info.default is not ...:
                defaults[name] = info.default
            else:
                # Required fields — provide dummy values that satisfy
                # their validators so we can isolate the target field.
                if annotation is str:
                    defaults[name] = "dummy"
                elif annotation is int:
                    defaults[name] = 0
                elif annotation is bool:
                    defaults[name] = False
                else:
                    defaults[name] = ""

        # Override host fields with valid-looking values.
        if "imap_host" in defaults:
            defaults["imap_host"] = "imap.example.com"
        if "smtp_host" in defaults:
            defaults["smtp_host"] = "smtp.example.com"
        if "username" in defaults:
            defaults["username"] = "user@example.com"

        defaults[field_name] = value
        MailConfig.model_validate(defaults)
    except Exception as exc:
        return f"{field_name!r}: {exc}"
    return None


class SettingsStore:
    """Per-account key-value settings store backed by SQLite.

    Wraps the ``component_settings`` table with masking for secrets
    and field-level validation against :class:`MailConfig`.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # -- read ---------------------------------------------------------------

    def is_empty(self, conn: object) -> bool:
        """Return ``True`` when no settings have been stored yet.

        The *conn* must be an open ``sqlite3.Connection`` to the
        per-account DB (the caller owns its lifecycle).
        """
        return component_settings_count(conn) == 0  # type: ignore[arg-type]

    def get_all(self, conn: object) -> dict[str, str]:
        """Return all stored settings as ``{key: value}``.

        Secret fields are masked as ``"***"``.  The *conn* must be an
        open ``sqlite3.Connection`` (the caller owns its lifecycle).
        """
        raw = get_all_component_settings(conn)  # type: ignore[arg-type]
        return {k: _masked_value(k, v) for k, v in raw.items()}

    def get(self, conn: object, key: str) -> str | None:
        """Return a single setting value, or ``None``.

        Secret fields are masked as ``"***"``.
        """
        from robotsix_auto_mail.db import get_component_setting

        raw = get_component_setting(conn, key)  # type: ignore[arg-type]
        if raw is None:
            return None
        return _masked_value(key, raw)

    # -- write --------------------------------------------------------------

    def update(
        self, conn: object, updates: dict[str, object]
    ) -> dict[str, str]:
        """Validate and apply *updates*.

        Returns a ``{field_name: error_message}`` dict for every field
        that failed validation (empty dict = all fields accepted).
        On partial failure, valid fields are still persisted.
        """
        errors: dict[str, str] = {}
        valid: dict[str, str] = {}

        for field_name, raw_value in updates.items():
            str_value = str(raw_value) if not isinstance(raw_value, str) else raw_value
            err = _validate_field(field_name, str_value)
            if err is not None:
                errors[field_name] = err
            else:
                valid[field_name] = str_value

        if valid:
            set_component_settings(conn, valid)  # type: ignore[arg-type]

        return errors

    # -- import -------------------------------------------------------------

    def seed_from_mail_config(self, conn: object, config: MailConfig) -> None:
        """Populate the store from a :class:`MailConfig` instance.

        Used by the one-time import to seed all settings at once.
        Secret fields are stored as their raw (unmasked) values.
        """
        settings: dict[str, str] = {}
        for field_name in MailConfig.model_fields:
            value = getattr(config, field_name)
            if isinstance(value, SecretStr):
                settings[field_name] = value.get_secret_value()
            elif isinstance(value, bool):
                settings[field_name] = str(value).lower()
            elif value is not None:
                settings[field_name] = str(value)
            else:
                settings[field_name] = ""
        set_component_settings(conn, settings)  # type: ignore[arg-type]

    def to_mail_config(self, conn: object) -> MailConfig | None:
        """Build a :class:`MailConfig` from the stored settings.

        Returns ``None`` when the store is empty (needs import).
        """
        if self.is_empty(conn):
            return None
        raw = get_all_component_settings(conn)  # type: ignore[arg-type]
        # Build kwargs, coercing types that differ from str.
        kwargs: dict[str, object] = {}
        for field_name, field_info in MailConfig.model_fields.items():
            raw_val = raw.get(field_name)
            if raw_val is None:
                continue
            annotation = field_info.annotation
            if annotation is SecretStr:
                kwargs[field_name] = SecretStr(raw_val)
            elif annotation is int:
                try:
                    kwargs[field_name] = int(raw_val)
                except ValueError:
                    kwargs[field_name] = field_info.default
            elif annotation is bool:
                kwargs[field_name] = raw_val.lower() in ("true", "1", "yes")
            else:
                kwargs[field_name] = raw_val
        return MailConfig.model_validate(kwargs)
