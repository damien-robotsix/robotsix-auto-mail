# Changelog

## 0.0.0 (unreleased)

- Removed dead backward-compat re-exports `_is_waste_folder` and
  `_parse_list_line` from `robotsix_auto_mail.imap` (they had zero callers
  importing via the package namespace).

- Added `pytest-args: --hypothesis-profile=ci -m "not docker"` to the CI
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
- Added `scripts/check_kind_literals.py` (no-op) to satisfy the
  `python-ci.yml` reusable workflow from `robotsix-mill`, which calls this
  script unconditionally but this repo does not use a `TicketKind` enum.

- Fixed `provider_model` parameter in `_run_llm_agent`, `detect_provider`, and
  `propose_archive_subfolder_llm` so that a non-None value is actually passed
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
  board at `mail.robotsix.net`. See `deploy/README.md`. Watchtower pins
  `DOCKER_API_VERSION=1.44` for Docker Engine 29+ compatibility; the nginx
  runbook uses the certbot `--nginx` installer and documents the UID-1000
  bind-mount ownership step.
