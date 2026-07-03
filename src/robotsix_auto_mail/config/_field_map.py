"""Canonical MailConfig field-name → dotted-YAML-path mapping.

Single source of truth shared by the component-agent config contract
(``_component_agent_config_contract._FIELD_YAML_MAP``) and the config-sync
checker (``scripts/config/check_config_sync.FIELD_TO_YAML``), so the two
cannot drift when a config field is added, renamed, or removed.
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
    "llm_api_key": "llm.api_key",  # pragma: allowlist secret
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
