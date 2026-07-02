# Changelog

## 0.0.0 (unreleased)

- Fix silent mail loss after IMAP ``UIDVALIDITY`` changes: ingestion now tracks
  the mailbox's ``UIDVALIDITY`` and, when the server renumbers UIDs (mailbox
  recreated/restored, some server maintenance), resets the stale ``imap_uid``
  watermark so a full ``ALL`` re-scan resumes ingestion (dedup by ``message_id``
  keeps it idempotent). Adds ``ImapClient.select_folder_and_uidvalidity`` and
  ``db.delete_watermark``.
- Fix incorrect install instructions: update `detect` error message to reference `uv sync --extra llm`, correct README Python version claim to 3.14, add explicit non-PyPI note, and remove `robotsix-autoupdate` from `[project.scripts]`.
- Dry-run ingestion no longer calls ``update_record_source`` on duplicate messages, preventing unintended DB mutations.
- Security: MSAL OAuth2 token cache file is now created with restrictive permissions (file 0600, directory 0700) so the refresh token is not readable by other local users on multi-user hosts.
- Security: added CSRF protection via Origin-header check in `BoardHandler._check_csrf` for all POST endpoints. Changed default server bind from `0.0.0.0` to `127.0.0.1` and added `--host` CLI flag to the `serve` subcommand for explicit opt-in to external access.
- SMTP client now passes ``timeout=60`` to all three connection constructors
  (direct-TLS, STARTTLS, plain), mirroring the IMAP client's timeout.
  Prevents a stalled server from blocking the sending thread indefinitely.
- Fix stored XSS in email detail view: escape the subject in the ``<title>`` tag (the ``<h1>`` was already escaped; this was the only unescaped sink).
- Fix silent data loss on config round-trip: `MailAccountsConfig.from_yaml` now reads the
  top-level `logging:` YAML section (level, format, file_dir) and applies it to every
  account, matching the existing behaviour for `llm:` and `langfuse:` sections.
- Replace dead `.robotsix-mill/periodic/data_dir_audit.yaml` with `.robotsix-mill/periodic/data_dir_gc.yaml` to enable the `data_dir_gc` built-in periodic workflow for stale-file detection and cleanup under `.data/`.
- Add CSS linting via stylelint to pre-commit config and a minimal `stylelint.config.mjs` extending `stylelint-config-standard`. Also extend `deno fmt` coverage to `.css` files in both pre-commit and CI.
- Extract `reconcile_records` from `pipeline/__init__.py` into its own module
  at `pipeline/reconcile.py`, re-exported for backward compatibility.
- Enable the `dockerfile` manager in Renovate configuration so that
  the `python:3.14-slim` base image digest in the `Dockerfile` is
  automatically updated when new patch versions are published.
- Enable `changelog_autofill` periodic runner to automatically insert changelog entries on PR branches where the changelog-enforcer CI check is failing.
- Split `tests/pipeline/test_pipeline.py` into domain-focused test modules:
  `test_fetch.py`, `test_ingest.py`, `test_reconcile.py`,
  `test_pipeline_cli_ingest.py`, and `_helpers.py`.
- Update `docs/architecture.md` to reflect parser consolidation into `pipeline/` — remove standalone `parser/` entry and update ingestion data flow reference from `parser.parse_message()` to `parse_message()`.
- Add structured feature request issue template (`.github/ISSUE_TEMPLATE/feature_request.yml`) with initial checks, description, and affected-areas sections.
- Consolidated the `parser` module into `pipeline`: moved `src/robotsix_auto_mail/parser/__init__.py` → `src/robotsix_auto_mail/pipeline/_parse.py`, updated all imports and the module taxonomy.
- Add `LLM_API_KEY` and `LLM_PROVIDER_MODEL` environment variable fallbacks
  in `resolve_llm_api_key` and `resolve_llm_provider_model`, making the
  resolution chain (arg → env var → config file) match the documented behavior
- Added tests for the `LLM_API_KEY` and `LLM_PROVIDER_MODEL` env var
  fallback and explicit-wins-over-env behavior in
  `tests/config/test_config_loader.py`.
- Added unit tests for the serve CLI subcommand and the background reconcile loop (`tests/cli/test_commands_serve.py`).
- Bump actions/checkout from v4 (34e1148) to v6 (df4cb1c) across all workflow files.
- Fix the ``lockfile.yml`` workflow: pass ``GITHUB_TOKEN`` so the
  "Commit updated lockfile" step can authenticate its ``git push``.

- Added ``robotsix-agent-comm`` to the ``dev`` extra so CI can run the
  component-agent config-contract tests.

- Bump ``astral-sh/setup-uv`` action from v8.1.0 to v8.2.0.

- Fixed ``detect --overwrite`` to preserve top-level ``llm:`` and ``langfuse:``
  sections from an existing config file (previously overwrite mode dropped
  them).  Also fixed the ``detect`` command to properly resolve and write the
  LLM API key and provider model (from argument, env var, or config file) into
  the output config, making it self-contained.

- Migrated ``add_column_if_missing`` and ``run_additive_migrations`` helpers
  from a local copy in ``db/_migrate.py`` to the fleet-shared
  ``robotsix_llmio.core.sqlite_utils`` module.

- Refresh the ``robotsix-agent-comm`` git pin (declared ``rev="main"``) from the
  stale locked commit ``c57e9d74`` to ``e5e6d85e`` so the optional
  ``[calendar]``/``[broker]`` extras can import ``ConfigContractError`` from
  ``robotsix_agent_comm.protocol``. Lockfile-only change; no behaviour change.
- Migrated from the ``Tier`` enum (removed from ``robotsix-llmio``) to a
  plain ``int`` level parameter: ``_run_llm_agent`` and all call sites
  (``config_sync_agent``, ``archive``, ``detect``, ``draft``, ``triage``)
  now accept ``level: int`` (where ``1`` = cheap, ``2`` = default) instead
  of ``tier: Tier``.  Pinned ``robotsix-llmio`` to the updated commit that
  removed ``Tier``.

- Configuration is now read **only from the YAML config file** — all
  environment-variable-based configuration has been removed. The single
  ``MAIL_CONFIG_PATH`` variable still *locates* the file (default
  ``config/mail.local.yaml``); it must use the multi-account ``accounts:``
  shape. Removed the ``MAIL_*`` / ``MAIL_ACCOUNTS_*`` / ``LLM_*`` /
  ``LANGFUSE_*`` / ``LOG_*`` config env vars, the single-account ("mono")
  config path (including the historical ``.data/mail.db`` default — each
  account uses ``.data/<id>/mail.db``), the ``migrate-config`` command, the
  ``.env.example`` file, and the env half of the config-sync checker.
  ``resolve_llm_api_key`` / ``resolve_llm_provider_model`` now resolve from an
  explicit argument then the config file's ``llm.*`` section (no env).

- Fix CodeQL code-scanning alerts: suppress false-positive unused-global-variable warnings on importable constants, replace ineffectual Ellipsis literals with ``pass`` in abstract/Protocol method stubs, and drop unused local variable in batch-op adapter.

- Replaced the triage agent's JSON "memory" ledgers with a single
  human-readable ``triage_rules.md`` file maintained by a fast ("flash") LLM.
  Whenever you act on a message (board move, archive-to-folder, save-draft,
  ``triage-set``), the flash LLM is given your action plus the mail's sender,
  subject, and body and rewrites the rules file only when a rule should
  change; the triage agent and archive-subfolder proposal read this file so
  triage reasons over the whole mail context. Removed the ``SenderMemory`` /
  ``ArchiveFolderMemory`` models and the ``triage_human_memory`` /
  ``archive_folder_memory`` watermark ledgers (the per-message archive
  override + LLM-hint caches are unchanged). The file lives at
  ``<db-dir>/triage_rules.md`` per account by default; override it with
  ``triage.rules_path`` (``MAIL_TRIAGE_RULES_PATH``). Web-board actions update
  the rules in a background thread (never blocking the action); ``triage-set``
  updates inline. Rule maintenance is best-effort and a no-op without a
  resolvable LLM API key.

- `render_accounts_yaml` now emits a top-level `logging:` section when
  `log_level`, `log_format`, or `log_file_dir` differ from their defaults,
  matching the existing behaviour for `llm:` and `langfuse:`.
- Preserve `component_agent.enabled` in per-account config rendering so that round-tripping (detect → write, or migrate-config) no longer silently drops the setting.
- Fix OpenSSF Scorecard workflow: move `id-token: write` from top-level to job-level permissions block
- Add `id-token: write` at the job level in `analysis` job in `.github/workflows/scorecard.yml` to satisfy `ossf/scorecard-action` `publish_results` requirement
- Add OpenSSF Scorecard integration (`.github/workflows/scorecard.yml`) — runs weekly and on pushes to `main`, publishing results via SARIF upload for GitHub code-scanning alerts.
- Switched CI dependency vulnerability audit from `pip-audit` to `uv audit --frozen`.
- Convert 11 remaining raw `init_db()`/`try`/`finally: conn.close()` call sites to use the `_with_db()` context manager from `server/_constants.py`. Affected files: `_auth_mixin.py`, `_config_mixin.py`, `_component_agent_responder.py`, `adapters.py`, `views/board.py`, `views/detail.py`, `handlers.py`.
- Add Deno-based JavaScript linting (`deno lint`) and formatting (`deno fmt --check`) for `board-auto-mail.js` via pre-commit hooks and a CI step in `repo-checks`.
- Add `security_posture` periodic workflow presence trigger (`.robotsix-mill/periodic/security_posture.yaml`)
- Removed the root ``CLAUDE.md`` orientation file; ``AGENT.md`` is now the
  sole agent-facing root document. Updated the references in ``AGENT.md`` and
  ``README.md`` and dropped the path from ``docs/modules.yaml``.

- Fixed the ``CI`` workflow, which had been ``startup_failure`` on every
  commit: the ``security`` job passed ``run-cyclonedx-sbom``, an input the
  pinned reusable ``python-security.yml`` does not declare, so GitHub rejected
  the whole run before any job started. Removed the unsupported input and
  cleared the lint/type findings the now-running gate surfaced (vulture,
  deptry ``DEP002`` for the unused ``robotsix-agent-comm``, plus ruff and
  ``mypy src/ --strict``).

- Removed dead code: ``ProviderEntry.in_managed_hosting`` (field and all 10
  constructor arguments), ``_ProtocolClient._oauth2_client_id`` and
  ``_oauth2_client_secret`` (parameters and instance attributes). Removed the
  corresponding vulture whitelist entries.

- Removed a stale vulture whitelist entry that referenced ``logger`` via a
  broken import path (``robotsix_auto_mail.config.logger`` is not re-exported
  by the package).

- Consolidated the `component-agent` module into `server`:
  moved `config_contract.py` → `_component_agent_config_contract.py` and
  `responder.py` → `_component_agent_responder.py`; updated all imports;
  removed the standalone `component-agent` module entry from
  `docs/modules.yaml`.

- Registered `.github/ISSUE_TEMPLATE/bug_report.yml` and
  `.github/ISSUE_TEMPLATE/config.yml` under the `ci` module in
  `docs/modules.yaml`.

- Added `.github/ISSUE_TEMPLATE/bug_report.yml` (YAML issue form with required
  Description, Steps to Reproduce, and Environment fields) and
  `.github/ISSUE_TEMPLATE/config.yml` (disables blank issues) so bug reports
  arrive with structured, actionable information.

- Documentation audit: corrected stale or inaccurate content across the
  ``docs/`` set against the current code — the triage action vocabulary and
  board column list in ``connecting.md``, the OAuth2/Microsoft 365,
  ``component_agent``, ``draft``, and calendar surfaces plus the ``pipeline``
  data-flow in ``architecture.md``, the schema/dedup/dry-run/log-format
  details in ``ingestion.md``, the renamed ``ingester`` service and heartbeat
  healthcheck in ``deployment.md``, the board HTTP-API shapes in ``skill.md``,
  the per-account ``component_agent`` config in ``configuration.md``, the CI
  job structure in ``testing.md``, the ``lgtm`` suppression locations in
  ``codeql-verification.md``, and the git-source/Docker-export/lockfile
  details in ``dependencies.md``. Removed the obsolete programming-language
  ADR (``docs/decisions/``) and the stale duplicate ``docs/CHANGELOG.md`` (the
  MkDocs site now links the canonical root ``CHANGELOG.md``); updated
  ``mkdocs.yml`` and ``docs/modules.yaml`` accordingly.

- IMAP and SMTP XOAUTH2 authentication now retries once with a force-refreshed
  MSAL token when the first attempt is rejected (e.g. due to Conditional Access
  or Continuous Access Evaluation).  CAE claims challenges from the server are
  forwarded to MSAL's ``acquire_token_silent`` for compliant token renewal.
  When a token remains rejected after force-refresh with a known AADSTS
  Conditional Access code (53000–53004, 530032), the raised ``ImapAuthError`` /
  ``SmtpAuthError`` message explicitly names "Conditional Access" so operators
  can distinguish a tenant-policy block from a credential problem.

- Fixed the Microsoft OAuth2 device-code flow to auto-probe account health
  before reporting success, so the "Account connection failure" warning banner
  disappears on the next page load without requiring a manual "Recheck
  connections" click.  The board JS now performs a full ``window.location.reload()``
  instead of the card-only ``refreshBoard()``, matching the already-displayed
  "✅ Connected! Reloading…" message.

- Consolidated deployment documentation into ``docs/deployment.md`` as the
  single entry point and removed ``deploy/README.md``. The deployment doc now
  describes the current **central-deploy contract** (``deploy/docker-compose.yml``,
  ``central-deploy-contract-version: 1``) — the ``robotsix.deploy.*`` labels,
  config provisioning via the gateway, and day-2 operations — replacing the
  obsolete Watchtower + in-repo nginx runbook (the referenced
  ``deploy/nginx/mail.robotsix.net.conf`` never existed). Updated the
  ``deploy`` module entry in ``docs/modules.yaml`` to match.

- Fixed ``determine_archive_structure`` and ``detect_provider`` docstrings to
  document the full three-step API key resolution chain (argument → env var
  → config file), matching ``generate_draft_reply``.

- Docs: expanded the Microsoft 365 OAuth2 onboarding documentation with
  three resolution paths for admin-consent errors (allowlist Thunderbird,
  custom app registration, app password fallback), added ``--oauth2-client-id``
  and ``--oauth2-tenant`` rows to the detect flag table, and documented
  ``--stdout`` + OAuth2 flag combination for scripting workflows.

- Board: Microsoft OAuth2 accounts can now be authorized / reconnected
  directly from the web board via an "Authorize / Reconnect" button in
  health-alert banners, using the device-code flow with a modal prompt.

- Added ``--oauth2-client-id`` and ``--oauth2-tenant`` flags to ``detect``,
  allowing operators to supply a custom Azure app registration for
  Microsoft 365 OAuth2 at detect-time instead of manually editing the
  written YAML.

- Added ``--app-password`` flag to ``detect``, enabling password/basic
  auth for Microsoft-hosted accounts where the tenant still allows
  legacy authentication (app passwords). Mutually exclusive with
  ``--oauth2-client-id`` / ``--oauth2-tenant``.

- Fixed ``MailConfig.from_env()`` to no longer require ``MAIL_PASSWORD``
  when ``MAIL_OAUTH2_PROVIDER=microsoft``, enabling env-var-only
  Microsoft 365 deployments that use MSAL/XOAUTH2 without a password.

- Removed five unwired ``COMPONENT_AGENT_*`` broker env var rows
  (``COMPONENT_AGENT_ID``, ``_BROKER_HOST``, ``_BROKER_PORT``,
  ``_BROKER_TOKEN``, ``_BROKER_TLS_CA``) from the "Component agent
  (global)" table in ``docs/configuration.md`` — only
  ``COMPONENT_AGENT_ENABLED`` is backed by code.  Also updated the
  multi-account globals list to reflect the single wired variable.

- Aligned ``logging:`` section handling with ``llm:`` / ``langfuse:``:
  per-account ``logging:`` blocks are no longer emitted by the YAML
  renderer and are now rejected by the loader with an actionable error
  (logging is application-wide, like llm and langfuse).

- Added the missing ``provider_model`` field to the ``llm:`` section of
  ``config/config.yaml``, restoring parity with the schema and the other
  config artifacts (``.env.example``, ``docs/config/mail.local.example.yaml``).

- Extracted the repeated ``init_db(...)`` / ``try:`` / ``finally: conn.close()``
  pattern into a shared ``_with_db()`` context manager in
  ``server/_constants.py``, replacing seven duplicate blocks across the
  action, view, triage, and draft mixins.  The one endpoint that
  intentionally ran without ``skip_migrations`` now passes
  ``skip_migrations=False`` explicitly.

- `_serve_board_content` now passes `config_failures` to
  `_build_board_content`
- Standalone CycloneDX SBOM generation as a workflow artifact in CI and
  release pipelines, enabling downstream tooling (Dependency-Track, OWASP
  Dependency-Check) to monitor the Python dependency tree independently of
  the container image.
