"""Migrate-config subcommand handler."""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

from robotsix_auto_mail.config import (
    DEFAULT_CONFIG_PATH,
    ConfigurationError,
    MailAccount,
    MailConfig,
    render_accounts_yaml,
)


def _cmd_migrate_config(args: argparse.Namespace) -> int:
    """Convert a deprecated single-account config file into the accounts shape.

    Idempotent: a file already in the multi-account shape is left untouched
    (exit 0).  A missing file is an error (exit 1).  A mono file is rewritten
    into a one-entry ``accounts:`` container preserving every value; the
    original is backed up to ``<path>.bak`` first.  ``--dry-run`` prints the
    migrated YAML and writes nothing (neither the file nor the backup).
    """
    from robotsix_yaml_config import (
        YamlConfigError,
        read_yaml_file,
    )

    path = Path(args.config) if args.config else Path(DEFAULT_CONFIG_PATH)
    account_id = args.id or "default"

    if not path.exists():
        sys.stderr.write(f"Error: config file not found: {path}\n")
        return 1

    try:
        data = read_yaml_file(path)
    except YamlConfigError as exc:
        sys.stderr.write(f"Error: invalid YAML in {path}: {exc}\n")
        return 1

    if isinstance(data, dict) and isinstance(data.get("accounts"), list):
        sys.stdout.write(
            f"{path} is already in the multi-account shape; nothing to do.\n"
        )
        return 0

    try:
        cfg = MailConfig.from_yaml(path, validate=False)
    except ConfigurationError as exc:
        sys.stderr.write(f"Error: cannot parse {path}: {exc}\n")
        return 1

    store = data.get("store") if isinstance(data, dict) else None
    has_store_path = isinstance(store, dict) and "path" in store
    if not has_store_path:
        cfg = dataclasses.replace(cfg, db_path=f".data/{account_id}/mail.db")

    account = MailAccount(account_id=account_id, config=cfg, label=account_id)
    banner = (
        "# Migrated to the multi-account shape by "
        "`robotsix-auto-mail migrate-config`.\n"
        "# The original single-account file was backed up with a .bak suffix."
    )
    migrated = render_accounts_yaml([account], account_id, banner=banner)

    if args.dry_run:
        sys.stdout.write(migrated)
        return 0

    backup = Path(f"{path}.bak")
    backup.write_text(path.read_text())
    path.write_text(migrated)
    sys.stdout.write(f"Backup written to {backup}\nMigrated config written to {path}\n")
    return 0
