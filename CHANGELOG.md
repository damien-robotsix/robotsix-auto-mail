# Changelog

## 0.0.0 (unreleased)

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
  `_build_board_content` so health-alert banners are rendered in
  the JSON response for config-load failures (previously the argument
  was omitted, leaving `health_alerts_html` always empty).

- Added commented `component_agent:` section to `config/config.yaml`
  (the managed deployment config skeleton), matching the field already
  documented in `.env.example`, `docs/configuration.md`, and
  `docs/config/mail.local.example.yaml`.

- Board now sets `account=__all__` cookie on fresh multi-account visits (no
  query param, no cookie) so the aggregate view persists across
  navigation — previously the cookie was only set on explicit
  `?account=__all__` requests.  The account picker in both board views
  now has a visible "Mailbox:" label.  `default_account` config comment
  and `MailAccountsConfig`/`_cmd_serve` docstrings clarified to note
  this field is for CLI/startup, not the board view default.

- Removed duplicate `TO_ARCHIVE` sort in `_gather_account_board_data` — the
  same in-place sort was applied twice to `column_buckets[TO_ARCHIVE]`, a
  copy-paste bug.  Only the first sort remains.

- Removed stale calendar and CI paths from `docs/modules.yaml` — the calendar
  package and `deps-bump.yml` workflow were already deleted.

- Removed dead `_get_bool` helper from `config/schema.py` — it had zero
  production callers after an earlier refactor.  Its four corresponding test
  functions in `tests/config/test_schema.py` were also removed.

- Enabled Renovate's `pre-commit` manager so `.pre-commit-config.yaml` hooks
  receive automatic version update PRs.

- Documented `MAIL_CONFIG_PATH` environment variable in `docs/configuration.md`.
- Fixed docstring cross-references in `run_triage_agent` from stale `load_llm`/`load_llm_provider_model` to `resolve_llm_api_key`/`resolve_llm_provider_model`.

- Re-established `component_agent` package with HTTP API routes (monitor,
  config-get, config-set) served directly by the board server without the
  agent-comm broker.  A new `component_agent_enabled` flag on
  `MailConfig` gates the responder; the `_ComponentAgentApiMixin`
  adds the three `/api/component-agent/*` endpoints to `BoardHandler`.
  Config contract validation (`ConfigContractError`, `apply_config_update`,
  `get_config_snapshot`) is preserved from the pre-removal code,
  adapted for HTTP error responses.  `COMPONENT_AGENT_ENABLED` env var
  and `component_agent.enabled` YAML key are documented in the examples.

- Removed stale `BOARD_AGENT_*` environment variable documentation from
  `docs/configuration.md` (these vars were already removed from code in a
  prior cleanup but the docs section was missed).
- Removed `board_agent` (in-repo mill board bridge) and `component_agent`
  (in-repo agent-comm responder); their equivalents now live outside this
  repo. All associated config fields, tests, docs, and dependencies
  (`robotsix-board-agent`) were removed. The board HTTP API is now
  documented in `docs/skill.md` for external agents that wish to drive the
  board directly over HTTP without a bridge.

## 0.0.0 (unreleased)

- Fixed ``docs/configuration.md``: the documented default for
  ``LLM_PROVIDER_MODEL`` was ``openrouter-deepseek`` but the code
  default is ``""`` (empty string). Corrected the Default column
  and clarified that an empty value delegates to the
  ``robotsix-llmio`` library's tier default.
- Fixed misleading comment in ``.env.example`` for ``LLM_PROVIDER_MODEL``:
  the default is ``""`` (empty string), not ``openrouter-deepseek``.
- Centralized three operational watermark keys (``triage_run:state``,
  ``batch_op:state``, ``reconcile:state``) into module-level constants
  in ``_constants.py``, replacing ~28 hardcoded string literals across
  8 modules.
- Added individual triage action string constants (``INBOX``,
  ``HUMAN_TRIAGE``, ``PENDING_ACTION``, ``TO_ARCHIVE``, ``TO_DELETE``,
  ``TO_CALENDAR``, ``TO_ANSWER``, ``DRAFT_READY``) in
  ``robotsix_auto_mail.triage._constants`` and re-exported them from
  ``robotsix_auto_mail.triage``.  All server-side call sites now import
  these constants instead of hardcoding the action strings, centralizing
  the vocabulary and eliminating ~44 duplicated string literals across 9
  files.
- **Breaking (default change):** ``MailConfig.llm_provider_model`` now
  defaults to ``""`` (was ``"openrouter-deepseek"``).  When unset, every
  LLM call resolves its model from the llmio tier/level defaults.  The
  field remains as an escape-hatch — set ``LLM_PROVIDER_MODEL`` in the
  environment or ``llm.provider_model`` in YAML to override.
- Removed ``provider_model`` from the central-deploy config template
  (``config/config.yaml``) and from the component-agent settings UI
  (``component_agent/config_contract.py``).

- Removed orphaned ``.robotsix-mill/periodic/langfuse_cleanup.yaml`` —
  Langfuse trace cleanup is now centralized in robotsix-mill (global_only).

- Registered ``config/config.yaml`` in the ``deploy`` module manifest
  (``docs/modules.yaml``).

- Extracted duplicate field-validation chains in ``MailConfig`` loaders into a
  shared ``_coerce_field`` helper, and unified repeated top-level
  section-extraction blocks in ``MailAccountsConfig.from_yaml`` via a new
  ``_extract_section_fields`` helper.

- Added ``image:`` fields to both services in ``docker-compose.yml`` so
  central-deploy can pull pre-built images rather than building from source.
  The ``build:`` blocks remain for local development; ``docker compose up``
  continues to build locally when no image is cached.

- Removed ``extract_calendar_summary`` from the public ``robotsix_auto_mail.calendar``
  package exports (``__init__.py`` and ``__all__``). The helper remains available
  internally at ``robotsix_auto_mail.calendar.schema.extract_calendar_summary``.

- Extracted shared row-materialization logic from ``list_records`` and
  ``list_untriaged_records`` into a private ``_rows_to_mailrecords`` helper.

- Fixed inaccurate ``provider_model`` and ``api_key`` docstrings in
  ``run_config_sync_agent`` and ``generate_draft_reply`` — both now
  document the actual tier-level-default fallback (via
  ``_run_llm_agent``) rather than a non-existent env-var cascade.

- Removed orphan ``get_record_by_correlation_id`` query function

- Extracted duplicated config-file fallback cascade in ``load_llm`` and
  ``load_llm_provider_model`` into shared private helper
  ``_load_file_config_optional``.
- Extracted duplicated DB-only batch-operation loop into shared
  ``_run_db_only_batch_op`` helper in ``server/adapters.py``.
  (never called — no production or test callers).

- Consolidated duplicate ``.ft-branch-leaf:hover`` and ``.ft-leaf:hover``
  CSS rules in ``board.css`` into a single comma-separated selector.

- Removed dead backward-compat re-exports ``DEFAULT_STATUS`` and
  ``row_to_mailrecord`` from ``db/__init__.py`` (zero callers via the
  package namespace).

- Extracted shared ``fetchJson`` helper in ``board-auto-mail.js``,
  replacing two duplicate fetch-then-json promise chains.
- Added ``validate-pyproject`` to pre-commit hooks and CI for semantic
  validation of ``pyproject.toml`` (PEP 621 fields and tool-specific
  subtables).
- Removed dead re-exports and ``__all__`` from
  ``robotsix_auto_mail.component_agent.__init__`` (all consumers import
  from submodules directly).
- Removed 4 dead backward-compat re-exports from
  ``robotsix_auto_mail.calendar``: ``build_calendar_transport``,
  ``build_calendar_transport_from_config``, ``build_ssl_context``,
  and ``DATE_TIME_RE`` — none had callers via the package namespace.
- Removed dead backward-compat re-exports (``_batch_banner_html``,
  ``_gather_account_board_data``, ``_render_board_columns``,
  ``_render_board_page_shell``) from
  ``robotsix_auto_mail.server.views.__init__``.  Tests now import these
  symbols directly from ``robotsix_auto_mail.server.views.board``.
- Removed three unused backward-compat re-exports from
  ``robotsix_auto_mail.triage``: ``_build_user_message``,
  ``_save_memory``, and ``normalize_archive_subfolder``.
  These are still available via their native submodules
  (``triage.agent``, ``triage.classifier``).

- Fixed three stale path references in ``CLAUDE.md``: the static
  directory path, ``_calendar_mixin.py``→``_action_mixin.py``, and
  ``board-auto-mail.js`` location.
- Fixed remaining stale subpackage path references in `docs/architecture.md`
  (`cli.py` → `cli/`, `server.py` → `server/`) and updated the dead
  `modules.schema.yaml` link to document the current `robotsix-modules
  check-registration` validation.
- Replaced inline LLM agent build boilerplate in
  ``robotsix_auto_mail.triage.classifier.propose_archive_subfolder_llm``
  with a call to the shared ``_run_llm_agent`` helper, eliminating ~35
  lines of duplicated lazy-import / TierConfig / build_agent / run_agent
  code.
- Added unit tests for ``src/robotsix_auto_mail/config/render.py``
  covering ``_yaml_scalar``, ``_render_account_block``, and
  ``render_accounts_yaml``.

- Fixed the ``provider_model`` parameter docstring in ``_run_llm_agent``
  to accurately describe the ``None`` fallback: it uses the tier-level
  default model, not a "standard resolution cascade".
- Fixed docstring gaps in `db/archive.py`: added missing `provider_model`
  parameter to `setup_archive`, added missing `archive_root` parameter to
  `determine_archive_structure`, and corrected the `provider_model` fallback
  description to reflect the actual tier-default behaviour.

- Fixed stale multi-account DB path defaults in `.env.example` to match
  the actual `.data/<id>/mail.db` form (was incorrectly documented as
  `.data/mail-<id>.db`).

- Consolidated the duplicated archive root constant `"robotsix-mail-archive"`
  into a single canonical definition `_ARCHIVE_ROOT` in `_constants.py`,
  imported by both `db/archive.py` and `config/schema.py`.

- Fixed the ``run_config_sync_agent`` docstring: the ``provider:``
  parameter was renamed to ``provider_model:`` to match the actual
  function signature.

- Refactored `detect_provider` in `detect/detector.py` to delegate LLM
  agent construction and execution to the shared `_run_llm_agent` helper,
  removing ~35 lines of duplicated boilerplate (API key resolution,
  TierConfig, provider lookup, agent build, run_agent).
- Derived `_MICROSOFT_HOSTS` from the canonical `_PROVIDER_DB` Microsoft
  entry rather than maintaining a duplicate hardcoded frozenset, so the
  two sources of truth can no longer drift apart. (ci: auto-fix changelog enforcer — add entry for remaining stale paths fix in docs/architecture.md)

- Removed dead backward-compat re-exports `_is_waste_folder` and
  `_parse_list_line` from `robotsix_auto_mail.imap` (they had zero callers
  importing via the package namespace).

- Added `pytest-args: --hypothesis-profile=ci -m "not docker"` to the CI
- Moved `docs/server/component-agent.md` to `docs/component_agent/component-agent.md` (per-module docs layout)
  workflow's `python-ci.yml` reusable workflow call, passing the Hypothesis
  profile and marker filter through to the shared test runner.

- Added "Component agent (global)" section to `docs/configuration.md`
  documenting all six `COMPONENT_AGENT_*` environment variables
  (enabled, ID, broker host/port/token/TLS CA).
- Enabled `UP` (pyupgrade) and `SIM` (flake8-simplify) ruff rule sets in
  `pyproject.toml`. Applied auto-fixes for 19 `UP` violations (quoted type
  annotations → unquoted, `datetime.timezone.utc` → `datetime.UTC`,
  `typing.Iterator`/`Callable` → `collections.abc`). The 15 remaining `SIM`
  violations (and 1 `UP047`) are left for follow-up manual resolution.
  `UP`/`SIM` rules are suppressed in `tests/` and `scripts/` via
  per-file-ignores.
- Added `scripts/ci/check_kind_literals.py` (no-op) to satisfy the
  `python-ci.yml` reusable workflow from `robotsix-mill`, which calls this
  script unconditionally but this repo does not use a `TicketKind` enum.

- Fixed `provider_model` parameter in `_run_llm_agent`, `detect_provider`, and
  `propose_archive_subfolder_llm` so that a non-None value is actually passed
- Fixed docstring of `detect_provider` to reference the correct parameter
  name (`provider_model` instead of `provider`) and describe the actual
  fallback (tier-level default model) instead of the stale env-var cascade.
  through to `get_provider_for_identifier` instead of being silently ignored
  in favor of the tier-level default model. Setting `LLM_PROVIDER_MODEL` now
  takes effect for all LLM agent calls.

- Replaced manual `Agent` construction and lifecycle in
  `dispatch_calendar_request()` with `BrokeredRequester` for brokered
  calendar transport, eliminating transport-pair creation, request send,
  reply unwrap, and teardown boilerplate.

- Removed 9 dead `_render_*` backward-compat re-exports from
  `src/robotsix_auto_mail/server/views/__init__.py`.
- Removed dead `ProviderEntry` re-export from
  `src/robotsix_auto_mail/detect/__init__.py`.

- Added `.robotsix-mill/periodic/env_doc_sync.yaml` to enable the
  `env_doc_sync` periodic workflow that cross-references env-var
  declarations against `docs/configuration.md`.
- Removed 12 dead private-symbol re-exports from
  `src/robotsix_auto_mail/triage/__init__.py`.
- Removed the remaining `_UNSUBSCRIBE_SUGGESTIONS_KEY` re-export from
  `src/robotsix_auto_mail/triage/__init__.py` that was missed in the
  prior 12-symbol cleanup, and updated its test import to use the
  direct `triage._constants` path.
- Updated module-path references in `docs/architecture.md` to reflect
  the flat-file → subpackage refactoring (13 stale references).
- Updated stale subpackage path references in `CLAUDE.md` (`pipeline.py` →
  `pipeline/__init__.py`, `server.py` → `server/` package, `cli.py` →
  `cli/__init__.py`).
- Removed dead backward-compat re-exports (`run_additive_migrations`, `_ADDITIVE_COLUMNS`)
  from `db/__init__.py`; no callers import them from the package namespace.
- Embedded an agent-comm component responder (`board-manager-robotsix-auto-mail`)
  with dedicated configuration contract, settings module, and test coverage.
- Added a `broker` optional-dependency extra (alias for the `calendar` extra's
  `robotsix-agent-comm`) for documentation clarity.
- Registered `component_agent/` and `tests/component_agent/` in the module
  taxonomy (`docs/modules.yaml`).
- Removed 21 dead backward-compat re-exports from
  `src/robotsix_auto_mail/config/__init__.py`.
- Extracted shared checkout + setup-uv steps into a composite action
  (`.github/actions/setup-project/action.yml`) and refactored
  `ci.yml`, `codeql.yml`, and `lockfile.yml` to use it.
- Added unit tests for `_run_llm_agent` (`tests/core/test_llm_agent.py`)
  covering happy path, missing API key, `run_agent` failure, and tier mapping.
- Moved `scripts/smoke_board.sh` to `scripts/server/smoke_board.sh` for
  per-module script layout alignment.
- Enabled the `uv` manager in `renovate.json` so Renovate bumps `uv.lock`
  alongside `pyproject.toml` dependency updates.
- Removed stale monolithic `tests/config/test_config.py`; all 66 tests are
  already covered by the split domain modules.
- Split `tests/server/test_action_mixin.py` (1727 lines, 51 tests) into 9
  per-action-handler test files plus a shared `_test_helpers.py` module
  (`_FakeHandler`, `_SyncThread`), following the same class-per-file pattern
  used for `test_server.py` and `test_board_handler.py`.
- Removed redundant lazy re-import of `delete_record_by_message_id` inside
  `_BoardActionMixin._archive_and_delete`.
- Moved `dev/auto-mail-autoupdate.sh` to `scripts/dev/auto-mail-autoupdate.sh`;
  updated self-locating logic for the new path.
- Merged `tracing` and `logging` modules into a single `observability` module with
  a unified `setup_observability(config)` entry point; moved tests to
  `tests/observability/`.
- Moved config example files (`config/.gitkeep`, `config/mail.local.example.yaml`)
  to `docs/config/` for per-module documentation alignment.
- Added `.github/PULL_REQUEST_TEMPLATE.md` with a structured contributor
  checklist (changelog, tests, type-checking, docs, pre-commit, module
  registration).
- Removed dead re-export of `get_provider_for_identifier` from
  `robotsix_auto_mail.detect` (no consumers exist; all callers import directly
  from `robotsix_llmio.core`).
- Removed 21 dead private re-exports from `server/__init__.py`; only `BoardHandler`
  and `make_board_handler` remain as public exports.
- Added `docs/configuration.md` — a comprehensive environment-variable reference
  covering all 41 configuration variables across seven categories (IMAP, SMTP,
  auth, storage, ingest, archive, triage, calendar, LLM, Langfuse, logging,
  board agent, and multi-account).
- Fixed stale key ``provider`` → ``provider_model`` in the YAML configuration example
  in ``docs/connecting.md``.
- Added AGENT.md with repository conventions for CI-fix and other automated agents.
- Fixed stale path in AGENT.md: `logging/__init__.py` → `observability/__init__.py`.
- Added changelog-enforcer convention to AGENT.md.
- Added structured access logging to the HTTP server via ``log_message``.
- Migrated logging to delegate core pipeline to ``robotsix_llmio.logging.setup_logging``
  (stream handler, formatter, OTel trace-id injection), retaining only the
  date-stamped file handler in the local ``setup_logging`` wrapper.
- Added changelog-enforcer CI job to gate pull requests.
- Refactored `config_sync_agent.py`'s `run_config_sync_agent` to accept an
  explicit `api_key` parameter, simplifying the call site and test surface.
- Split monolithic `tests/imap/test_imap.py` into domain-focused test modules
  (`test_imap_auth.py`, `test_imap_connection.py`, `test_imap_cross_folder.py`,
  `test_imap_encoding.py`, `test_imap_errors.py`, `test_imap_folders.py`,
  `test_imap_messages.py`).
- Migrated LLM agent call sites from ``get_provider_for_identifier`` +
  ``provider.build_agent(level=...)`` to ``robotsix_llmio.core.create_model`` +
  ``provider.build_agent(level=...)``, removing the dependency on
  ``resolve_llm_provider_model`` from the config module.
- Initial package scaffold.
- IMAP/SMTP mail automation with triage and kanban workflows.
- Continuous deployment for `server.robotsix.net`: `release.yml` now publishes
  a moving `main` image on every push to `main`, and a new `deploy/` stack
  (Watchtower auto-update + nginx TLS/basic-auth reverse proxy) serves the
  board at `mail.robotsix.net`. See `docs/deployment.md`. Watchtower pins
  `DOCKER_API_VERSION=1.44` for Docker Engine 29+ compatibility; the nginx
  runbook uses the certbot `--nginx` installer and documents the UID-1000
  bind-mount ownership step.
