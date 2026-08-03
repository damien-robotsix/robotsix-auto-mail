"""Canonical MailConfig field-name → dotted-YAML-path mapping.

Single source of truth shared by the config-sync
checker (``scripts/config/check_config_sync.FIELD_TO_YAML``), so the two
cannot drift when a config field is added, renamed, or removed.

Only *per-mailbox* fields appear here.  The component-wide LLM settings
(the canonical ``langfuse`` / ``openrouter`` blocks and
``models`` tier overrides) live on ``MailAccountsConfig``, outside any
account, and are therefore outside this map.
"""

from __future__ import annotations

FIELD_YAML_MAP: dict[str, str] = {
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
    "ingest_interval_minutes": "ingest.interval_minutes",
    "ingest_mode": "ingest.mode",
    "heartbeat_file": "ingest.heartbeat_file",
    "archive_root": "archive.root",
    "archive_enabled": "archive.enabled",
    "triage_on_ingest": "triage.on_ingest",
    "triage_rules_path": "triage.rules_path",
    "log_level": "logging.level",
    "log_format": "logging.format",
}
