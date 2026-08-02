"""``SettingsStore`` — per-account key-value settings backed by SQLite.

Secrets (fields whose name ends with ``_key``, ``_secret``, ``password``,
or ``_token``) are stored in plain text inside the DB (the DB file lives
on the component's own encrypted volume) but are **masked** as ``"***"``
in every read path — both the Python API and the JSON response from
GET /settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr

from robotsix_auto_mail.config.model import MailConfig

if TYPE_CHECKING:
    from robotsix_auto_mail.config.model import MailAccount, MailAccountsConfig
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
            if info.is_required():
                # Required fields — provide dummy values that satisfy
                # their validators so we can isolate the target field.
                ann = info.annotation
                if ann is str:
                    defaults[name] = "dummy"
                elif ann is int:
                    defaults[name] = 0
                elif ann is bool:
                    defaults[name] = False
                else:
                    defaults[name] = ""
            else:
                defaults[name] = info.default

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

    def update(self, conn: object, updates: dict[str, object]) -> dict[str, str]:
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


def discover_accounts_from_settings_stores(
    data_dir: str = ".data",
) -> list[MailAccount]:
    """Discover accounts from existing per-account settings stores.

    Scans *data_dir* for subdirectories containing a ``mail.db`` file,
    reads each DB's ``component_settings`` table via :class:`SettingsStore`,
    and reconstructs :class:`MailAccount` objects from non-empty stores.

    Accounts that fail to load (corrupt DB, missing required fields) are
    skipped with a warning log.  This function is a fallback for when the
    main config file (``config/config.json``) has been overwritten by the
    deploy system — accounts added via the web UI can still be recovered
    from their settings stores.

    Returns:
        A (possibly empty) list of discovered :class:`MailAccount` objects.
    """
    import logging
    import sqlite3
    from pathlib import Path

    from robotsix_auto_mail.config.model import MailAccount

    logger = logging.getLogger(__name__)
    discovered: list[MailAccount] = []
    data_path = Path(data_dir)

    if not data_path.is_dir():
        return discovered

    for entry in sorted(data_path.iterdir()):
        if not entry.is_dir():
            continue
        db_file = entry / "mail.db"
        if not db_file.is_file():
            continue

        account_id = entry.name
        try:
            conn = sqlite3.connect(str(db_file))
            try:
                store = SettingsStore(str(db_file))
                if store.is_empty(conn):
                    continue
                mail_config = store.to_mail_config(conn)
                if mail_config is None:
                    continue
                # Ensure the db_path reflects the actual file location.
                mail_config = mail_config.model_copy(update={"db_path": str(db_file)})
                discovered.append(
                    MailAccount(
                        account_id=account_id,
                        config=mail_config,
                    )
                )
                logger.info(
                    "Discovered account %r from settings store at %s",
                    account_id,
                    db_file,
                )
            finally:
                conn.close()
        except Exception:
            logger.warning(
                "Failed to load settings store for account %r at %s",
                account_id,
                db_file,
                exc_info=True,
            )

    return discovered


def merge_settings_store_accounts(
    accounts: MailAccountsConfig,
) -> MailAccountsConfig:
    """Merge accounts discovered from per-account settings stores into *accounts*.

    Scans ``.data/`` for per-account databases whose settings stores contain
    a full :class:`MailConfig`, and adds any account whose ``account_id`` is
    not already present in *accounts*.  The original *accounts* is never
    mutated — a new :class:`MailAccountsConfig` is returned.

    This is the shared merge step used by the serve command, the ingester
    watch loop, and the background reconcile loop so that accounts added via
    the web UI survive a deploy-system overwrite of ``config/config.json``
    in every code path, not just the board.

    When *accounts* has no ``default_account_id`` and the merge adds the
    first account, that account becomes the default.
    """
    import logging

    from robotsix_auto_mail.config.model import MailAccountsConfig

    logger = logging.getLogger(__name__)

    discovered = discover_accounts_from_settings_stores()
    existing_ids = set(accounts.ids())
    new_discovered = [a for a in discovered if a.account_id not in existing_ids]

    if not new_discovered:
        return accounts

    logger.info(
        "Merging %d account(s) discovered from settings stores: %s",
        len(new_discovered),
        [a.account_id for a in new_discovered],
    )

    merged_accounts = list(accounts.accounts) + new_discovered
    default_id = accounts.default_account_id
    if not default_id and merged_accounts:
        default_id = merged_accounts[0].account_id

    return MailAccountsConfig(
        accounts=merged_accounts,
        default_account_id=default_id,
    )
