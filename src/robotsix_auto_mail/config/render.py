"""Multi-account YAML rendering.

Serialises :class:`~robotsix_auto_mail.config.model.MailAccount` objects
back into the multi-account YAML config file shape.  Used by ``detect`` (to
write/append a detected account) and by ``migrate-config`` (to convert a
deprecated single-account file).  Depends only on
:mod:`robotsix_auto_mail.config.model`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from robotsix_auto_mail.config.model import MailAccount, MailConfig


def _yaml_scalar(value: object) -> str:
    """Render *value* as a YAML scalar.

    Booleans and integers are emitted bare; strings are always double-quoted
    (a valid, lossless YAML representation that safely escapes any special
    characters, empty strings, and values that would otherwise be parsed as a
    non-string).
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value))


def _render_account_block(account: MailAccount, indent: str) -> list[str]:
    """Render one :class:`MailAccount` as a YAML list item under ``accounts:``.

    The mandatory ``imap`` / ``smtp`` / ``auth`` / ``store`` sections are
    always emitted; the optional sections (``ingest`` / ``archive``
    / ``triage`` and the OAuth2 fields) are emitted only when
    they carry a non-default value, so freshly-detected configs stay terse
    while migrated configs preserve any customised value.

    ``llm:`` and ``langfuse:`` are NOT emitted per-account — they are
    application-wide and rendered as top-level sections by
    :func:`render_accounts_yaml`.
    """
    cfg = account.config
    defaults = MailConfig(imap_host="", smtp_host="", username="", password="")
    item = indent + "  "
    lines = [f"{indent}- id: {_yaml_scalar(account.account_id)}"]
    if account.label:
        lines.append(f"{item}label: {_yaml_scalar(account.label)}")
    lines.append(f"{item}imap:")
    lines.append(f"{item}  host: {_yaml_scalar(cfg.imap_host)}")
    lines.append(f"{item}  port: {cfg.imap_port}")
    lines.append(f"{item}  tls_mode: {_yaml_scalar(cfg.imap_tls_mode)}")
    lines.append(f"{item}  folder: {_yaml_scalar(cfg.imap_folder)}")
    lines.append(f"{item}smtp:")
    lines.append(f"{item}  host: {_yaml_scalar(cfg.smtp_host)}")
    lines.append(f"{item}  port: {cfg.smtp_port}")
    lines.append(f"{item}  tls_mode: {_yaml_scalar(cfg.smtp_tls_mode)}")
    lines.append(f"{item}auth:")
    lines.append(f"{item}  username: {_yaml_scalar(cfg.username)}")
    if cfg.oauth2_provider:
        # MSAL-managed OAuth2 (Microsoft 365): no password is stored;
        # tokens live in the per-account MSAL cache.
        lines.append(f"{item}  oauth2_provider: {_yaml_scalar(cfg.oauth2_provider)}")
        lines.append(f"{item}  oauth2_tenant: {_yaml_scalar(cfg.oauth2_tenant)}")
    else:
        # This function intentionally writes secrets to a YAML config file
        # that is stored with restrictive permissions (0600).
        # lgtm[py/clear-text-storage-sensitive-data]
        lines.append(f"{item}  password: {_yaml_scalar(cfg.password)}")
    if cfg.oauth2_token:
        lines.append(f"{item}  oauth2_token: {_yaml_scalar(cfg.oauth2_token)}")
    if cfg.oauth2_client_id:
        lines.append(f"{item}  oauth2_client_id: {_yaml_scalar(cfg.oauth2_client_id)}")
    if cfg.oauth2_client_secret:
        lines.append(
            f"{item}  oauth2_client_secret: {_yaml_scalar(cfg.oauth2_client_secret)}"
        )
    lines.append(f"{item}store:")
    lines.append(f"{item}  path: {_yaml_scalar(cfg.db_path)}")
    if cfg.ingest_interval_minutes != defaults.ingest_interval_minutes:
        lines.append(f"{item}ingest:")
        lines.append(f"{item}  interval_minutes: {cfg.ingest_interval_minutes}")
    if (
        cfg.archive_root != defaults.archive_root
        or cfg.archive_namespace != defaults.archive_namespace
        or cfg.archive_enabled != defaults.archive_enabled
    ):
        lines.append(f"{item}archive:")
        lines.append(f"{item}  root: {_yaml_scalar(cfg.archive_root)}")
        lines.append(f"{item}  namespace: {_yaml_scalar(cfg.archive_namespace)}")
        lines.append(f"{item}  enabled: {_yaml_scalar(cfg.archive_enabled)}")
    if cfg.triage_on_ingest != defaults.triage_on_ingest:
        lines.append(f"{item}triage:")
        lines.append(f"{item}  on_ingest: {_yaml_scalar(cfg.triage_on_ingest)}")
    if (
        cfg.log_level != defaults.log_level
        or cfg.log_format != defaults.log_format
        or cfg.log_file_dir != defaults.log_file_dir
    ):
        lines.append(f"{item}logging:")
        lines.append(f"{item}  level: {_yaml_scalar(cfg.log_level)}")
        lines.append(f"{item}  format: {_yaml_scalar(cfg.log_format)}")
        lines.append(f"{item}  file_dir: {_yaml_scalar(cfg.log_file_dir)}")
    return lines


def render_accounts_yaml(
    accounts: Sequence[MailAccount],
    default_account_id: str,
    *,
    banner: str = "",
) -> str:
    """Render *accounts* as a multi-account YAML config file.

    Emits top-level ``llm:`` / ``langfuse:`` sections (application-wide)
    followed by ``default_account:`` and an ``accounts:`` list.
    Used by ``detect`` (to write/append a detected account) and by
    ``migrate-config`` (to convert a deprecated single-account file).
    """
    lines: list[str] = []
    if banner:
        lines.append(banner.rstrip("\n"))
        lines.append("")

    # Emit top-level llm: / langfuse: sections using the first account's
    # config values (they are identical across all accounts by construction).
    representative = accounts[0].config
    if (
        representative.llm_api_key
        or representative.llm_provider_model != "openrouter-deepseek"
    ):
        lines.append("llm:")
        if representative.llm_api_key:
            # Writing the API key to a YAML config file is intentional;
            # the file is stored with restrictive permissions (0600).
            # lgtm[py/clear-text-storage-sensitive-data]
            lines.append(f"  api_key: {_yaml_scalar(representative.llm_api_key)}")
        if representative.llm_provider_model != "openrouter-deepseek":
            lines.append(
                f"  provider_model: {_yaml_scalar(representative.llm_provider_model)}"
            )
        lines.append("")
    if (
        representative.langfuse_public_key
        or representative.langfuse_secret_key
        or representative.langfuse_base_url
    ):
        lines.append("langfuse:")
        lines.append(
            f"  public_key: {_yaml_scalar(representative.langfuse_public_key)}"
        )
        # Writing the secret key to a YAML config file is intentional;
        # the file is stored with restrictive permissions (0600).
        # lgtm[py/clear-text-storage-sensitive-data]
        lines.append(
            f"  secret_key: {_yaml_scalar(representative.langfuse_secret_key)}"
        )
        lines.append(f"  base_url: {_yaml_scalar(representative.langfuse_base_url)}")
        lines.append("")

    lines.append(f"default_account: {_yaml_scalar(default_account_id)}")
    lines.append("")
    lines.append("accounts:")
    for account in accounts:
        lines.extend(_render_account_block(account, "  "))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
