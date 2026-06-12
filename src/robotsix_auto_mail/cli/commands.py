"""Per-command handlers and local rendering helpers for the CLI."""

from __future__ import annotations

import argparse
import dataclasses
import errno
import json
import sys
from pathlib import Path
from typing import TextIO

import robotsix_auto_mail.cli as _cli
from robotsix_auto_mail.cli.config import (
    _account_id_from_email,
    _detect_settings,
    _existing_account_ids,
    _get_password,
    _verify_and_refine,
)
from robotsix_auto_mail.config import (
    DEFAULT_CONFIG_PATH,
    ConfigurationError,
    MailAccount,
    MailAccountsConfig,
    MailConfig,
    render_accounts_yaml,
)
from robotsix_auto_mail.db import (
    MailRecord,
    get_record_by_message_id,
    list_records,
)
from robotsix_auto_mail.format import _BODY_PREVIEW_LIMIT, _format_date
from robotsix_auto_mail.imap import ImapClient, ImapError
from robotsix_auto_mail.pipeline import IngestResult, ingest_folder
from robotsix_auto_mail.smtp import (
    SmtpClient,
    SmtpError,
)


def _print_header(file: TextIO, title: str, width: int = 60, char: str = "-") -> None:
    file.write(f"\n{title}\n{char * width}\n")


def _cmd_probe(config: MailConfig) -> int:
    """Run the probe subcommand: connect to IMAP + SMTP and print metadata.

    Returns 0 when both succeed, 1 when either fails.
    """
    failures = 0

    # -- IMAP ---------------------------------------------------------------
    _print_header(sys.stdout, "IMAP Probe")

    try:
        with ImapClient(config) as imap:
            greeting = imap.server_greeting
            if greeting is not None:
                sys.stdout.write(
                    f"Greeting: {greeting.decode('utf-8', errors='replace')}\n"
                )
            else:
                sys.stdout.write("Greeting: (none)\n")

            sys.stdout.write("Capabilities:\n")
            for cap in imap.capabilities:
                sys.stdout.write(f"  - {cap}\n")

            # Folders
            sys.stdout.write("\nFolders:\n")
            folders = imap.list_folders()
            if folders:
                for fi in folders:
                    attrs = " ".join(fi.attributes) if fi.attributes else "(none)"
                    delim = fi.delimiter if fi.delimiter else "(none)"
                    sys.stdout.write(f"  {fi.name}\n")
                    sys.stdout.write(f"    attributes: {attrs}\n")
                    sys.stdout.write(f"    delimiter:  {delim}\n")
            else:
                sys.stdout.write("  (no folders returned)\n")
    except ImapError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        failures += 1

    # -- SMTP ---------------------------------------------------------------
    _print_header(sys.stdout, "SMTP Probe")

    try:
        with SmtpClient(config) as smtp:
            ehlo = smtp.ehlo_response
            if ehlo is not None:
                sys.stdout.write(
                    f"EHLO response: {ehlo.decode('utf-8', errors='replace')}\n"
                )
            else:
                sys.stdout.write("EHLO response: (none)\n")

            sys.stdout.write("\nESMTP features:\n")
            features = smtp.esmtp_features
            if features:
                for key, value in sorted(features.items()):
                    sys.stdout.write(f"  {key}: {value}\n")
            else:
                sys.stdout.write("  (no features)\n")

            # Deliberately: no send() call.  This is diagnostic-only.
    except SmtpError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        failures += 1

    return 0 if failures == 0 else 1


def _ingest_cycle(config: MailConfig, *, dry_run: bool = False) -> int:
    """Run a single ingest pass: fetch, parse, store, and update watermark.

    Returns 0 when the pipeline runs (including per-message errors),
    or 1 for a fatal connection failure (ImapClient raise).
    """
    result: IngestResult | None = None
    conn = _cli.init_db(config.db_path)
    try:
        with _cli.ImapClient(config) as imap_client:
            result = _cli.ingest_mail(conn, imap_client, config, dry_run=dry_run)
    except Exception:
        # Fatal connection failure — ImapClient(config) raised.
        result = None
    finally:
        conn.close()

    # If ImapClient(config) raised before ingest_mail ran, result is None.
    if result is None:
        return 1

    # -- Print summary -------------------------------------------------------
    if dry_run:
        sys.stdout.write("DRY RUN — nothing stored\n")

    sys.stdout.write(f"Fetched: {result.total_fetched:>2} messages\n")
    sys.stdout.write(f"Stored:  {result.stored:>2} new\n")
    sys.stdout.write(f"Skipped: {result.skipped:>2} duplicate\n")
    sys.stdout.write(f"Triaged: {result.triaged:>2}\n")
    sys.stdout.write(f"Errors:  {len(result.errors):>2}\n")

    if result.errors:
        for err_obj in result.errors:
            # Guard against empty message_id.
            mid = f" ({err_obj.message_id})" if err_obj.message_id else ""
            sys.stdout.write(f"  UID {err_obj.uid}{mid}: {err_obj.error}\n")

    return 0


def _cmd_ingest(
    accounts: MailAccountsConfig,
    *,
    account_id: str | None = None,
    all_accounts: bool = False,
    dry_run: bool = False,
    watch: bool = False,
) -> int:
    """Run the ingest subcommand for one or more accounts.

    When *account_id* is given, only that account is processed (exiting 1
    with the valid ids on an unknown id).  Otherwise every configured account
    is processed in order, regardless of *all_accounts* (a single-account
    container yields exactly one account, so single-account usage is
    unchanged).  A per-account header is printed only when more than one
    account is processed.

    In watch mode it loops forever, running an ingest cycle for each selected
    account every interval.  A failed cycle is logged and the loop continues;
    Ctrl-C exits cleanly with 0.
    """
    if account_id is not None:
        try:
            selected = [accounts.get(account_id)]
        except ConfigurationError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            sys.exit(1)
    else:
        selected = list(accounts.accounts)

    show_header = len(selected) > 1

    if not watch:
        rc = 0
        for account in selected:
            if show_header:
                sys.stdout.write(f"=== account: {account.account_id} ===\n")
            if _cli._ingest_cycle(account.config, dry_run=dry_run) != 0:
                rc = 1
        return rc

    interval_minutes = max(1, selected[0].config.ingest_interval_minutes)
    sys.stdout.write(
        f"Watch mode: ingesting every {interval_minutes} min (Ctrl-C to stop).\n"
    )
    sys.stdout.flush()
    try:
        while True:
            for account in selected:
                if show_header:
                    sys.stdout.write(f"=== account: {account.account_id} ===\n")
                try:
                    _cli._ingest_cycle(account.config, dry_run=dry_run)
                except Exception as exc:  # never let one bad cycle kill the loop
                    sys.stderr.write(f"Ingest cycle failed: {exc}\n")
            sys.stdout.write(f"Next ingest in {interval_minutes} min.\n")
            sys.stdout.flush()
            _cli.time.sleep(interval_minutes * 60)
    except KeyboardInterrupt:
        sys.stdout.write("\nWatch stopped.\n")
        return 0


_SEPARATOR = "-" * 60 + "\n"


def _render_card(record: MailRecord, file: TextIO) -> None:
    """Render a single mail record to *file*."""
    # Sender
    file.write(f"From:    {record.sender}\n")

    # Subject
    subject = record.subject if record.subject.strip() else "(no subject)"
    file.write(f"Subject: {subject}\n")

    # Date
    file.write(f"Date:    {_format_date(record.date)}\n")

    # Body preview
    body = record.body_plain
    if not body or not body.strip():
        preview = "(no body)"
    elif len(body) > _BODY_PREVIEW_LIMIT:
        preview = body[:_BODY_PREVIEW_LIMIT] + "…"
    else:
        preview = body
    file.write(f"\n{preview}\n")


def _render_board(records: list[MailRecord], file: TextIO) -> None:
    """Render every *record* in the inbox board view to *file*."""
    if not records:
        file.write("Your inbox is empty.\n")
        return

    for i, record in enumerate(records):
        if i > 0:
            file.write(_SEPARATOR)
        _render_card(record, file)

    count = len(records)
    file.write(f"{count} message(s)\n")


def _cmd_board(config: MailConfig) -> int:
    """Run the board subcommand: display ingested mail in a read-only view.

    Returns 0 on success, 1 on failure to load configuration.
    """
    conn = _cli.init_db(config.db_path)
    try:
        records = list_records(conn)
    finally:
        conn.close()

    _print_header(sys.stdout, "Inbox")
    _render_board(records, sys.stdout)

    return 0


def _cmd_detect(args: argparse.Namespace) -> int:
    """Run the detect subcommand: auto-detect provider settings, write the
    config, and verify it by connecting — refining with autoconfig, the LLM,
    and finally a manual prompt when the servers cannot be reached.
    Returns 0 on success, 1 on any error.
    """
    try:
        from robotsix_auto_mail.detect import (
            DetectionError,
            autoconfig_lookup,
            detect_provider,
            is_microsoft_provider,
            mx_lookup,
            provider_from_mx,
            provider_to_config,
        )
    except ImportError:
        sys.stderr.write(
            "The 'detect' command requires the pydantic-ai package. "
            "Install it with: pip install robotsix-auto-mail[dev]\n"
        )
        return 1

    from robotsix_auto_mail.config import load_llm

    account_id = args.id or _account_id_from_email(args.email)
    label = args.email

    api_key = load_llm()
    provider, mx_hosts = _detect_settings(
        args.email,
        api_key,
        autoconfig_lookup,
        mx_lookup,
        provider_from_mx,
        detect_provider,
        DetectionError,
    )
    if provider is None:
        return 1
    microsoft = is_microsoft_provider(provider)
    # Microsoft 365 rejects password auth; it uses MSAL-managed XOAUTH2, so we
    # never prompt for or write a password for these accounts.
    if microsoft:
        password: str | None = None
    else:
        password = _get_password(args)
        if password is None:
            return 1
    if args.stdout:
        config = dataclasses.replace(
            provider_to_config(
                provider,
                args.email,
                password="",  # nosec B106 - intentionally omitted from stdout
            ),
            db_path=f".data/{account_id}/mail.db",
        )
        if microsoft:
            sys.stderr.write(
                f"# Detected Microsoft 365 settings for {args.email} — "
                "OAuth2 (XOAUTH2); no password is used.\n"
                "# Save this as config/mail.local.yaml, then run:\n"
                f"#   robotsix-auto-mail auth login --account {account_id}\n"
                "# to complete the device-code consent and seed the token "
                "cache.\n"
            )
        else:
            sys.stderr.write(
                f"# Detected settings for {args.email} — verify before using.\n"
                "# The password was intentionally omitted: fill in auth.password "
                "or set the MAIL_PASSWORD env var before use.\n"
                "# Save this as config/mail.local.yaml.\n"
            )
        account = MailAccount(account_id=account_id, config=config, label=label)
        sys.stdout.write(render_accounts_yaml([account], account_id))
        return 0

    output_path = Path(args.output)
    if account_id in _existing_account_ids(output_path):
        sys.stderr.write(
            f"Error: account {account_id!r} already exists in {output_path}. "
            "Pass --id <new-id> to add a different account, or edit the file "
            "directly.\n"
        )
        return 1
    return _verify_and_refine(
        provider,
        email=args.email,
        api_key=api_key,
        mx_hosts=mx_hosts,
        output_path=output_path,
        password=password,
        password_from_args=args.password,
        no_verify=args.no_verify,
        account_id=account_id,
        label=label,
        provider_to_config=provider_to_config,
        detect_provider=detect_provider,
        _detection_error=DetectionError,
        microsoft=microsoft,
    )


def _cmd_migrate_config(args: argparse.Namespace) -> int:
    """Convert a deprecated single-account config file into the accounts shape.

    Idempotent: a file already in the multi-account shape is left untouched
    (exit 0).  A missing file is an error (exit 1).  A mono file is rewritten
    into a one-entry ``accounts:`` container preserving every value; the
    original is backed up to ``<path>.bak`` first.  ``--dry-run`` prints the
    migrated YAML and writes nothing (neither the file nor the backup).
    """
    from robotsix_yaml_config import (  # type: ignore[import-untyped]
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


def _cmd_config_sync(args: argparse.Namespace) -> int:
    """Run the config-drift advisory agent and render its proposals.

    This is an advisory tool, not a CI gate: a successful run returns 0
    even when drift proposals are found.  Returns 1 only on error (missing
    pydantic_ai, ConfigSyncError, missing API key).
    """
    try:
        from robotsix_auto_mail.config.config_sync_agent import (
            ConfigSyncError,
            run_config_sync_agent,
        )
    except ImportError:
        sys.stderr.write(
            "The 'config-sync' command requires the pydantic-ai package. "
            "Install it with: pip install robotsix-auto-mail[dev]\n"
        )
        return 1

    # Resolve the dedup connection only when --dedup is requested; like
    # detect, the advisory tool should not require a full mail config to run.
    conn = None
    if args.dedup:
        config = _load_config_or_exit(args.account)
        conn = _cli.init_db(config.db_path)

    try:
        result = run_config_sync_agent(api_key=args.api_key, conn=conn)
    except ConfigSyncError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1

    if args.output_format == "json":
        sys.stdout.write(json.dumps(result.model_dump(), indent=2) + "\n")
        # Advisory tool: a non-empty result is informational, not a gate.
        return 0

    _print_header(sys.stdout, "Config Drift Advisory")
    if not result.proposals:
        sys.stdout.write("No config drift detected.\n")
        # Advisory tool: a non-empty result is informational, not a gate.
        return 0

    for proposal in result.proposals:
        sys.stdout.write(f"\n{proposal.title}\n")
        sys.stdout.write(f"  confidence: {proposal.confidence}\n")
        field = proposal.affected_field if proposal.affected_field else "(none)"
        sys.stdout.write(f"  affected field: {field}\n")
        sys.stdout.write(f"\n{proposal.body}\n")

    # Advisory tool: a non-empty result is informational, not a gate.
    return 0


def _cmd_triage(args: argparse.Namespace) -> int:
    """Run the inbox-triage agent and render the recorded decisions.

    This is an advisory tool, not a CI gate: a successful run returns 0 even
    when triage decisions are produced.  Returns 1 only on error (missing
    pydantic_ai, TriageError).
    """
    try:
        from robotsix_auto_mail.triage import TriageError, run_triage_agent
    except ImportError:
        sys.stderr.write(
            "The 'triage' command requires the pydantic-ai package. "
            "Install it with: pip install robotsix-auto-mail[dev]\n"
        )
        return 1

    config = _load_config_or_exit(args.account)
    conn = _cli.init_db(config.db_path)
    try:
        decisions = run_triage_agent(
            conn, api_key=args.api_key, user_email=config.username
        )
    except TriageError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    finally:
        conn.close()

    if args.output_format == "json":
        payload = [d.model_dump() for d in decisions]
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return 0

    _print_header(sys.stdout, "Inbox Triage")
    if not decisions:
        sys.stdout.write("No inbox mail to triage.\n")
        return 0

    for decision in decisions:
        sys.stdout.write(f"\n{decision.message_id}\n")
        sys.stdout.write(f"  action: {decision.action}\n")
        sys.stdout.write(f"  confidence: {decision.confidence}\n")
        reason = decision.reason if decision.reason else "(none)"
        sys.stdout.write(f"  reason: {reason}\n")

    return 0


def _cmd_triage_folder(args: argparse.Namespace) -> int:
    """Ingest an existing mailbox folder one-shot, then triage the new mail.

    Fetches every message from the named *folder*, stores them locally
    (dedup by Message-ID, the INBOX watermark left untouched), then runs
    the triage agent over the newly-stored mail and renders the advisory
    decisions.  Triage is advisory and local-only: no mail is moved in the
    mailbox.  Returns 0 on success, 1 on error (missing pydantic_ai,
    ImapError, TriageError).
    """
    try:
        from robotsix_auto_mail.triage import (
            TriageDecision,
            TriageError,
            run_triage_agent,
        )
    except ImportError:
        sys.stderr.write(
            "The 'triage-folder' command requires the pydantic-ai package. "
            "Install it with: pip install robotsix-auto-mail[dev]\n"
        )
        return 1

    config = _load_config_or_exit(args.account)
    conn = _cli.init_db(config.db_path)
    decisions: list[TriageDecision] = []
    try:
        try:
            with ImapClient(config) as imap:
                result = ingest_folder(
                    conn, imap, config, args.folder, dry_run=args.dry_run
                )
        except ImapError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            return 1

        if not args.dry_run:
            try:
                decisions = run_triage_agent(
                    conn, api_key=args.api_key, user_email=config.username
                )
            except TriageError as exc:
                sys.stderr.write(f"Error: {exc}\n")
                return 1
    finally:
        conn.close()

    if args.output_format == "json":
        payload = {
            "folder": args.folder,
            "fetched": result.total_fetched,
            "stored": result.stored,
            "skipped": result.skipped,
            "errors": len(result.errors),
            "decisions": [d.model_dump() for d in decisions],
        }
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return 0

    # -- text: ingest summary (same style as _ingest_cycle) --------------
    if args.dry_run:
        sys.stdout.write("DRY RUN — nothing stored\n")
    sys.stdout.write(f"Fetched: {result.total_fetched:>2} messages\n")
    sys.stdout.write(f"Stored:  {result.stored:>2} new\n")
    sys.stdout.write(f"Skipped: {result.skipped:>2} duplicate\n")
    sys.stdout.write(f"Errors:  {len(result.errors):>2}\n")

    _print_header(sys.stdout, "Folder Triage")
    if not decisions:
        sys.stdout.write("No mail to triage.\n")
        return 0

    for decision in decisions:
        sys.stdout.write(f"\n{decision.message_id}\n")
        sys.stdout.write(f"  action: {decision.action}\n")
        sys.stdout.write(f"  confidence: {decision.confidence}\n")
        reason = decision.reason if decision.reason else "(none)"
        sys.stdout.write(f"  reason: {reason}\n")

    return 0


def _cmd_triage_set(args: argparse.Namespace) -> int:
    """Record a user triage decision for a single message.

    Returns 0 on success, 1 when the message_id is unknown or the action is
    invalid.
    """
    try:
        from robotsix_auto_mail.triage import (
            VALID_TRIAGE_ACTIONS,
            TriageError,
            record_human_decision,
            set_triage_decision,
        )
    except ImportError:
        sys.stderr.write(
            "The 'triage-set' command requires the pydantic-ai package. "
            "Install it with: pip install robotsix-auto-mail[dev]\n"
        )
        return 1

    if args.action not in VALID_TRIAGE_ACTIONS:
        sys.stderr.write(
            f"Error: invalid action {args.action!r}. "
            f"Must be one of {sorted(VALID_TRIAGE_ACTIONS)}\n"
        )
        return 1

    config = _load_config_or_exit(args.account)
    conn = _cli.init_db(config.db_path)
    try:
        if get_record_by_message_id(conn, args.message_id) is None:
            sys.stderr.write(f"Error: no mail with message_id {args.message_id!r}\n")
            return 1
        try:
            set_triage_decision(conn, args.message_id, args.action, source="user")
            record_human_decision(conn, args.message_id, args.action)
        except TriageError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            return 1
    finally:
        conn.close()

    sys.stdout.write(
        f"Recorded user triage decision: {args.message_id} -> {args.action}\n"
    )
    return 0


def _cmd_triage_rules(args: argparse.Namespace) -> int:
    """Propose deterministic triage rules and list the active rules.

    Deterministic — derived from triage history, no LLM / pydantic-ai
    required.  This is advisory: a successful run returns 0 even when new
    proposals are found.
    """
    from robotsix_auto_mail.triage import (
        _load_active_rules,
        _rule_fingerprint,
        propose_triage_rules,
        record_and_filter_rule_proposals,
    )

    config = _load_config_or_exit(args.account)
    conn = _cli.init_db(config.db_path)
    try:
        proposals = propose_triage_rules(conn)
        new_proposals = record_and_filter_rule_proposals(conn, proposals)
        active_rules = _load_active_rules(conn)
    finally:
        conn.close()

    if args.output_format == "json":
        payload = {
            "proposals": [
                {**p.model_dump(), "fingerprint": _rule_fingerprint(p)}
                for p in new_proposals
            ],
            "active_rules": [r.model_dump() for r in active_rules],
        }
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return 0

    _print_header(sys.stdout, "Triage Rule Proposals")
    if not new_proposals:
        sys.stdout.write("No new triage rule proposals.\n")
    else:
        for proposal in new_proposals:
            sys.stdout.write(f"\n{proposal.title}\n")
            sys.stdout.write(f"  fingerprint: {_rule_fingerprint(proposal)}\n")
            sys.stdout.write(f"  confidence: {proposal.confidence}\n")
            sys.stdout.write(
                f"  rule: {proposal.match_type}={proposal.match_value} "
                f"-> {proposal.action}\n"
            )
            sys.stdout.write(f"\n{proposal.body}\n")

    sys.stdout.write("\nActive rules:\n")
    if not active_rules:
        sys.stdout.write("  (none)\n")
    else:
        for rule in active_rules:
            sys.stdout.write(
                f"  {rule.match_type}={rule.match_value} -> {rule.action}\n"
            )
    return 0


def _cmd_triage_rules_set(args: argparse.Namespace) -> int:
    """Accept or reject a proposed triage rule by fingerprint.

    Deterministic — no LLM / pydantic-ai required.  Returns 0 on success,
    1 when the fingerprint is unknown or the state is invalid.
    """
    from robotsix_auto_mail.triage import TriageError, set_rule_state

    valid_states = {"accepted", "rejected"}
    if args.state not in valid_states:
        sys.stderr.write(
            f"Error: invalid state {args.state!r}. "
            f"Must be one of {sorted(valid_states)}\n"
        )
        return 1

    config = _load_config_or_exit(args.account)
    conn = _cli.init_db(config.db_path)
    try:
        set_rule_state(conn, args.fingerprint, args.state)
    except TriageError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    finally:
        conn.close()

    sys.stdout.write(
        f"Recorded triage rule state: {args.fingerprint} -> {args.state}\n"
    )
    return 0


def _cmd_config_sync_set(args: argparse.Namespace) -> int:
    """Record a user decision for a single config-drift finding.

    Returns 0 on success, 1 when the fingerprint is unknown or the state is
    invalid.
    """
    try:
        from robotsix_auto_mail.config.config_sync_agent import (
            _VALID_LEDGER_STATES,
            ConfigSyncError,
            set_finding_state,
        )
    except ImportError:
        sys.stderr.write(
            "The 'config-sync-set' command requires the pydantic-ai package. "
            "Install it with: pip install robotsix-auto-mail[dev]\n"
        )
        return 1

    if args.state not in _VALID_LEDGER_STATES:
        sys.stderr.write(
            f"Error: invalid state {args.state!r}. "
            f"Must be one of {sorted(_VALID_LEDGER_STATES)}\n"
        )
        return 1

    config = _load_config_or_exit(args.account)
    conn = _cli.init_db(config.db_path)
    try:
        set_finding_state(conn, args.fingerprint, args.state)
    except ConfigSyncError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    finally:
        conn.close()

    sys.stdout.write(
        f"Recorded config-drift finding state: {args.fingerprint} -> {args.state}\n"
    )
    return 0


def _cmd_auth_login(args: argparse.Namespace) -> int:
    """Run the OAuth2 device-code login for an account, seeding the cache.

    Resolves the account's :class:`MailConfig` (honouring its per-account
    ``db_path``), requires an OAuth2 provider, and runs the device-code
    consent flow so subsequent silent token refresh works.  Returns 0 on
    success and non-zero on any error (unknown/ambiguous account, a
    non-OAuth2 account, missing ``msal``, or a device-flow failure) — no
    traceback.
    """
    from robotsix_auto_mail.oauth2 import (
        MICROSOFT_PROVIDER,
        cache_path_for,
        device_code_login,
    )

    accounts = _load_accounts_or_exit()
    if args.account is not None:
        try:
            account = accounts.get(args.account)
        except ConfigurationError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            return 1
    elif len(accounts.accounts) == 1:
        account = accounts.accounts[0]
    else:
        sys.stderr.write(
            "Error: multiple accounts configured; pass --account <id>. "
            f"Available ids: {list(accounts.ids())!r}\n"
        )
        return 1

    config = account.config
    if config.oauth2_provider != MICROSOFT_PROVIDER:
        sys.stderr.write(
            "Error: `auth login` only applies to OAuth2 providers; account "
            f"{account.account_id!r} has no auth.oauth2_provider set.\n"
        )
        return 1

    try:
        device_code_login(config)
    except ConfigurationError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    except Exception as exc:  # device-flow error / user abort
        sys.stderr.write(f"Error: device-code login failed: {exc}\n")
        return 1

    sys.stdout.write(
        f"Device-code login complete. Token cache: {cache_path_for(config)}\n"
    )
    return 0


def _cmd_serve(
    accounts: MailAccountsConfig, *, default_account_id: str, port: int
) -> int:
    """Run the serve subcommand: start the web board HTTP server.

    The full *accounts* container drives per-request account resolution;
    *default_account_id* names the account served when a request omits
    ``?account=``.  Returns 0 on clean shutdown, 1 if the port is already
    in use.
    """
    from http.server import HTTPServer

    from robotsix_auto_mail.server import make_board_handler

    default = accounts.get(default_account_id)
    handler_class = make_board_handler(
        default.config.db_path,
        mail_config=default.config,
        accounts=accounts,
        default_account_id=default_account_id,
    )

    print(f"Serving board on http://0.0.0.0:{port}/board")
    try:
        # Binding to 0.0.0.0 is intentional: ``serve_board`` is a local dev
        # convenience tool, not a production server.
        server = HTTPServer(("0.0.0.0", port), handler_class)  # noqa: S104  # nosec B104
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down.")
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(f"Port {port} is already in use.", file=sys.stderr)
            return 1
        raise
    return 0


def _load_accounts_or_exit() -> MailAccountsConfig:
    """Load the accounts container, or print to stderr and exit 1 on failure."""
    try:
        return _cli.load_accounts()
    except Exception as exc:
        sys.stderr.write(f"Error loading configuration: {exc}\n")
        sys.exit(1)


def _load_config_or_exit(account_id: str | None = None) -> MailConfig:
    """Select one account's :class:`MailConfig`, or exit 1 on failure.

    With *account_id* given, returns that account's config (exiting 1 with the
    valid ids on an unknown id).  With *account_id* ``None`` the **default
    account** is returned (``default_account_id``, which falls back to the
    first account when ``default_account`` is unset), so single-mailbox usage
    never needs ``--account`` and multi-account usage selects the default.
    """
    accounts = _load_accounts_or_exit()
    if account_id is not None:
        try:
            return accounts.get(account_id).config
        except ConfigurationError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            sys.exit(1)
    return accounts.default.config
