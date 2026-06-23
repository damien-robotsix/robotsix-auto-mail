# Changelog

## 0.0.0 (unreleased)

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
