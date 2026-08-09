# Changelog

## [0.2.1](https://github.com/damien-robotsix/robotsix-auto-mail/compare/v0.2.0...v0.2.1) (2026-08-09)


### Bug Fixes

* **changelog:** drop the fragment that reddened main ([#1132](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1132)) ([f02229c](https://github.com/damien-robotsix/robotsix-auto-mail/commit/f02229cf78cd3f06f3e480f0bc253489e5f623b6))
* Chat-facing archive browse & move endpoints (list archived mails per folder, move mail between archive folders) (20260808T232819Z-chat-facing-archive-browse-move-endpoint-9c6b) ([#1136](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1136)) ([c1758ba](https://github.com/damien-robotsix/robotsix-auto-mail/commit/c1758baef166ab01363c6d43f6d8530a3e5cab5b))
* **ci:** let a non-Python fix actually clear main ([#1134](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1134)) ([664780e](https://github.com/damien-robotsix/robotsix-auto-mail/commit/664780e5fcc7fe2b45ffd7d8e4a53905c555dc14))
* Re-triage on restart/update must not clobber operator decisions (20260808T235207Z-re-triage-on-restart-update-must-not-clo-6fbe) ([#1137](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1137)) ([85950f7](https://github.com/damien-robotsix/robotsix-auto-mail/commit/85950f73337794fd9dbd94f969e75693a5e210f1))

## [0.2.0](https://github.com/damien-robotsix/robotsix-auto-mail/compare/v0.1.0...v0.2.0) (2026-08-08)


### ⚠ BREAKING CHANGES

* **config:** own LLM credentials in the canonical component-wide blocks ([#1054](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1054))
* **config:** load configuration only from the YAML file; remove env-based config ([#718](https://github.com/damien-robotsix/robotsix-auto-mail/issues/718))

### Features

* adopt the standard config surface and the shared settings panel ([#1051](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1051)) ([622017d](https://github.com/damien-robotsix/robotsix-auto-mail/commit/622017df2cc40bc1e8d1aae93c634103deb9b674))
* **config:** load configuration only from the YAML file; remove env-based config ([#718](https://github.com/damien-robotsix/robotsix-auto-mail/issues/718)) ([2af4c47](https://github.com/damien-robotsix/robotsix-auto-mail/commit/2af4c47d87e5162a0adaa9b26fd9df571e9f468e))
* **config:** own LLM credentials in the canonical component-wide blocks ([#1054](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1054)) ([f65fea1](https://github.com/damien-robotsix/robotsix-auto-mail/commit/f65fea1f42a301aa37584b7dd754690f6514993b))
* **release:** static version and release-please, drop hatch-vcs ([#1123](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1123)) ([63e1ef4](https://github.com/damien-robotsix/robotsix-auto-mail/commit/63e1ef48605c1521d7a54f2b6f1b62f775e0bac3))
* **triage:** replace JSON memory ledgers with a flash-LLM-maintained triage_rules.md ([#704](https://github.com/damien-robotsix/robotsix-auto-mail/issues/704)) ([8e1d4e1](https://github.com/damien-robotsix/robotsix-auto-mail/commit/8e1d4e145962f0a3878a7c726dcfa7877ea78280))


### Bug Fixes

* bind board server to 0.0.0.0 in compose so the gateway can reach it ([#765](https://github.com/damien-robotsix/robotsix-auto-mail/issues/765)) ([20686e9](https://github.com/damien-robotsix/robotsix-auto-mail/commit/20686e953d878c7ab26970c9cee8ad5a2319561c))
* **board:** adopt robotsix-board's move-control removal ([#1050](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1050)) ([83e3e7a](https://github.com/damien-robotsix/robotsix-auto-mail/commit/83e3e7a79bcf2d7038f7d563ebfe8531703e5017))
* **ci:** a failed coverage comment must not fail CI ([#1053](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1053)) ([5d2cd8b](https://github.com/damien-robotsix/robotsix-auto-mail/commit/5d2cd8b55fa380a10d5e44f7fb190099f3895a71))
* **ci:** drop the phantom 'not docker' marker filter, enforce strict markers ([#1116](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1116)) ([4e08498](https://github.com/damien-robotsix/robotsix-auto-mail/commit/4e0849851da6c539ea53cd13449528985574a53c))
* **ci:** grant the Docs caller the permissions the Pages spine needs ([#1095](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1095)) ([00b65dc](https://github.com/damien-robotsix/robotsix-auto-mail/commit/00b65dced5dc4b46b994e019623af6f8a7ac63e6))
* **deploy:** run the ingester in watch mode so it stops restart-looping ([#1026](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1026)) ([60b194f](https://github.com/damien-robotsix/robotsix-auto-mail/commit/60b194f0bf573b3e3be0f4e833e890d2abecff1e))
* **docker:** bump uv to 0.12.1 so it can parse uv.lock revision 3 ([#1106](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1106)) ([3ee66f2](https://github.com/damien-robotsix/robotsix-auto-mail/commit/3ee66f218b73043f17a0fb924727466228e894ca))
* **docker:** supply the package version the build context cannot derive ([#1108](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1108)) ([0fc63d5](https://github.com/damien-robotsix/robotsix-auto-mail/commit/0fc63d540d1c6f733e30de07e843c004815887dd))
* **release:** mint an App token so release PRs get CI ([#1125](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1125)) ([51a5218](https://github.com/damien-robotsix/robotsix-auto-mail/commit/51a521873f3f55e9434364ac1d69f275779c9fd9))
* **release:** regenerate uv.lock on the release branch ([#1127](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1127)) ([552a2ee](https://github.com/damien-robotsix/robotsix-auto-mail/commit/552a2eec400d5382a4b333af76392ca6eca0b037))
* **server:** allow same-origin framing so the dashboard iframe renders ([#1110](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1110)) ([1a31efd](https://github.com/damien-robotsix/robotsix-auto-mail/commit/1a31efda75df6fec0c6604527dd20bdb3c40f4bb))
* **server:** serve requests account-less when zero accounts are configured ([#970](https://github.com/damien-robotsix/robotsix-auto-mail/issues/970)) ([efdf69e](https://github.com/damien-robotsix/robotsix-auto-mail/commit/efdf69e0bfe6ed49e7bff7234ae7fd658b10caac))


### Documentation

* audit and correct docs/ against current code; drop obsolete docs ([#697](https://github.com/damien-robotsix/robotsix-auto-mail/issues/697)) ([686c455](https://github.com/damien-robotsix/robotsix-auto-mail/commit/686c455972d8ded2621151b07f0f001b5affd5cb))
* consolidate deploy docs into docs/deployment.md, drop deploy/README ([#688](https://github.com/damien-robotsix/robotsix-auto-mail/issues/688)) ([91cd9b1](https://github.com/damien-robotsix/robotsix-auto-mail/commit/91cd9b18dc9972664044665ab9ad62e1febe54e3))
* point out-of-docs links at GitHub instead of relative paths ([#1097](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1097)) ([31fd46b](https://github.com/damien-robotsix/robotsix-auto-mail/commit/31fd46b0827002a7cc24e8f2e78a616e1c132551))

## 0.1.0 (2026-08-08)

### Features

- Add HTTP security headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Content-Security-Policy`) to all board server responses via `_send_response` and `_redirect`. Harden the account session cookie with `HttpOnly` and `SameSite=Lax` attributes (and `Secure` when behind an HTTPS-terminating reverse proxy). (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260803T024649Z-add-http-security-headers-and-harden-ses-fefe)
- Enable the `trace_review` periodic to flag anomalous Langfuse traces from LLM-driven agent runs (triage, unsubscribe detection, config-sync). (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260720T114055Z-robotsix-auto-mail-enable-trace-review-p-4e76)
- POST action endpoints (`/move`, `/archive`, `/delete`, `/save-notes`, batch
  operations, etc.) now accept JSON request bodies in addition to form-encoded
  data, so clients sending ``Content-Type: application/json`` receive the same
  behaviour as the board UI. Malformed JSON with a JSON content type returns a
  clear 400 error.
  Remove the spurious `changelog.d/*.md` glob from the `core` module's `paths` list in `docs/modules.yaml` — the `changelog.d/` directory does not exist; only `changelog/` does. This was a regression from a previous fix that was not properly persisted. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260807T115145Z-accept-json-bodies-on-post-action-endpoi-d855)
- Add `description` to every config schema field so robotsix-ui tooltips show real help text instead of the raw key name. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260807T123602Z-fleet-wide-add-descriptions-to-all-confi-69e4)
- Mark rarely-changed config settings as "advanced" in the JSON Schema so the
  central-deploy Configure UI can hide them behind its "Show advanced settings"
  toggle.  Only 15 expert/tuning fields are flagged; must-set hostnames,
  Secrets, and ``log_level`` remain always-visible. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260721T132546Z-classify-config-settings-as-advanced-per-a917)
- Add inlined exponential-backoff retry primitives to autoconfig and MX-lookup HTTP calls for transient network error resilience (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260723T132809Z-migrate-robotsix-auto-mail-to-consume-ro-cc78)
- Extract MIME message construction from `SmtpClient.send()` into a pure `build_plain_text_message()` function in a new `mime` module, making MIME building testable without an SMTP client and reusable by other callers. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T150328Z-extract-mime-message-composition-from-sm-19a6)
- Added ``output_retries`` parameter to ``_run_llm_agent``, allowing callers to control pydantic-ai output-validation retry budget.  Also added automatic single retry on ``UnexpectedModelBehavior`` (model format slips) to convert transient format flakiness from a fatal error into a recoverable one. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260806T154238Z-agent-limitation-the-trace-review-sub-ag-9589)
- Enable the `survey` periodic agent to discover similar projects, study their approaches, and propose concrete improvements for the repo. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260718T184436Z-robotsix-auto-mail-enable-survey-periodi-c635)
- Ship `config/config.json` as the default configuration template and declare
  `robotsix.deploy.config-target` labels on both services in
  `deploy/docker-compose.yml` for compliance with the robotsix config standard. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260721T185654Z-config-standard-compliance)
- Replace the bespoke `/settings` API and hand-written settings panel with the
  fleet's standard config surface (`GET`/`PUT /config`, `GET /config/versions`,
  `POST /config/rollback`) over `config/config.json`, and mount the shared
  `@robotsix/ui` config panel on the Settings page instead of rendering a form
  of auto-mail's own. Secrets are now typed from the `SecretStr` fields on the
  model rather than guessed from field-name suffixes, updates are partial with
  merge-on-write, and every write is versioned with rollback (history never
  stores a secret value). (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T191409Z-adopt-shared-config-panel-and-standard-surface)
- Move the LLM provider key and the Langfuse credentials out of every mailbox
  and into the two canonical component-wide blocks robotsix-standards fixes:
  top-level `langfuse` (instance `host` plus a `projects` map keyed by Langfuse
  project name) and `openrouter` (a `keys` map addressed by the same aliases),
  alongside a component-wide `llm_provider_model`. auto-mail declares one LLM
  function, `robotsix-auto-mail`.

  Per-account `llm_api_key`, `llm_provider_model` and `langfuse_public_key` /
  `langfuse_secret_key` / `langfuse_base_url` are **removed** from `MailConfig`.
  A mailbox is not an LLM function, so N mailboxes meant N copies of one
  credential and, at best, one function's traces split across N projects. The
  deployment engine reads the canonical blocks and nothing else, so in that
  shape auto-mail reported no projects and no keys to the fleet at all —
  cost-monitor could not reconcile its spend and the chat agent's trace proxy
  had nothing to proxy, while auto-mail's own tracing kept working and hid it.

  Existing config files need no migration step: the old per-account keys are
  ignored on load, and the new blocks default to unconfigured. Re-enter the
  credentials once, in the component's Settings panel or in `config.json`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T210000Z-canonical-credential-blocks)
- Added a "Detect Settings" button to the web Add Mail Account form that auto-detects IMAP/SMTP host, port, and TLS settings from the entered email address using the existing ``config/detect`` autoconfig and MX-lookup logic. Detected fields remain editable so the operator can override them, and detection failures fall back to manual entry with a clear error message. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260728T212327Z-wire-existing-provider-auto-detection-in-a0dd)
- Board header toolbar + add-account in settings. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T220457Z-board-header-toolbar-layout-surface-add-8790)
- Added `GET /chat-skill` endpoint returning `text/markdown` with YAML frontmatter (`name`, `description`) and API documentation + safety rules, per the robotsix-standards chat-access standard §1.  Added `robotsix.deploy.chat-access: "true"` compose label on the board service so the component appears in the chat agent's roster. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T221406Z-make-robotsix-auto-mail-chat-access-comp-3cd9)

### Bug fixes

- Add ``trusted_origins`` configuration field to ``MailAccountsConfig`` for explicit CSRF origin allowlisting when the server runs behind a reverse proxy that rewrites ``Host`` without setting ``X-Forwarded-Host``. Fixes ``Forbidden: cross-origin request rejected`` on batch-delete and other POST actions from the board UI at ``https://mail.deploy.robotsix.net``. ([#1121](https://github.com/damien-robotsix/robotsix-auto-mail/issues/1121))
- Dropped `--randomly-seed=last` from the CI pytest arguments. The seed was
  resolved from `.pytest_cache`, which CI never persists, so each xdist worker
  resolved it independently and could disagree on collection order — tripping
  xdist's consistency guard and turning main red intermittently. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/ci-drop-randomly-seed-last)
- Fixed ten documentation links that pointed outside the docs directory with
  relative paths (`../config/config.example.json`, `../entrypoint.sh`, and
  similar). They were dead for anyone reading the published site, and they failed
  the strict docs build — which nobody saw, because the Docs workflow had never
  run. They now point at the files on GitHub. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/docs-external-links)
- Fixed the Docs workflow, which had never once run. Its caller granted
  `contents: write`, but the shared docs spine deploys through the Pages Actions
  and needs `contents: read` plus `pages: write` and `id-token: write`. A caller's
  permissions map replaces rather than merges, so all three were unmet — and an
  unmet request fails the run at startup, producing no logs and no checks, which
  is why nothing surfaced it. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/docs-pages-permissions)
- Serve `X-Frame-Options: SAMEORIGIN` and add `frame-ancestors 'self'` to the Content-Security-Policy, so the mail UI renders inside the central-deploy dashboard's same-origin iframe. It previously sent `X-Frame-Options: DENY`, which browsers honour by rendering the frame blank with no visible error. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/frame-options-sameorigin)
- Restore the container image build. `hatch-vcs` derives the package version from git, but the Docker build context excludes `.git`, so `uv pip install .` failed with `LookupError: Error getting the version from source 'vcs'`. The release workflow now passes the version it already knows as a `PACKAGE_VERSION` build argument (the tag for releases, `0.0.0.dev0+<sha>` otherwise), consumed via `SETUPTOOLS_SCM_PRETEND_VERSION`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/hatch-vcs-build-version)
- The mail ingester no longer crash-loops when no account has ``ingest_mode: watch``.
  It now starts healthy and idle, waiting for accounts to be added via the web UI
  or config file. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260801T000222Z-mail-ingester-no-longer-crash-loops-when-no-account-has-ing)
- Web UI add-account now seeds the per-account settings store and initializes the
  new account's database immediately, ensuring the account config is persisted in
  the managed configuration plane. The reconcile loop reloads accounts from the
  config file on every cycle so newly added accounts begin fetching mail without
  a restart. On boot, accounts discovered from existing settings stores are merged
  with config-file accounts so web-UI-added accounts survive even when the deploy
  system overwrites ``config/config.json``. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260801T004610Z-newly-added-mail-account-not-visible-in-e75a)
- Fix 17 instances of Python 2 `except X, Y:` syntax that silently failed to catch all listed exception types in Python 3. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260724T030450Z-fix-17-instances-of-python-2-except-x-y-c52a)
- Fixed review feedback: removed stale `changelog.d/*.md` glob from `docs/modules.yaml` and cleaned up trailing whitespace in the settings page script block. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260808T075758Z-settings-panel-must-render-the-auto-gene-4652)
- Fix mail board side-panel close button (×) not closing the panel. The `closeDetail` function used `location.hash = ""` to clear the URL fragment, which triggered a `hashchange` event whose handler called `closeDetail` again — creating a re-entrant cycle that prevented the panel from closing. Replaced with `history.replaceState` which clears the hash without firing `hashchange`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260808T081836Z-mail-board-side-panel-close-button-does-3455)
- Fix intermittent board rendering: only the first column would sometimes appear without action buttons or folder grouping. Root cause was a race condition where the board data gathering performed multiple independent SELECT queries (each its own implicit transaction), allowing a concurrently-running triage agent to produce inconsistent snapshots. Fixed by wrapping all board reads in a single explicit read transaction so every query observes the same point-in-time state. Also hardened the drawer-stripping logic in ``_render_board_columns`` to use a stable prefix match instead of exact string matching on the drawer, which had silently broken when the ``robotsix_board`` library added ARIA attributes. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260807T113457Z-mail-board-intermittently-renders-only-t-5160)
- Honour ``X-Forwarded-Host`` and ``Forwarded: host=`` headers in CSRF
    origin check so browser POSTs behind the fleet reverse proxy are no
    longer rejected with 403 Forbidden. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260731T122824Z-re-file-check-csrf-must-honor-x-forwarde-1983)
- Tell the triage-rules flash LLM which actions the automated triage agent
  can actually assign (HUMAN_TRIAGE, TO_ARCHIVE, TO_DELETE, TO_ANSWER) so it
  does not suggest rules using human-only actions like TO_CALENDAR.
  Also fix the DEFAULT_RULES_HEADER example that incorrectly referenced TO_CALENDAR. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260720T123355Z-flash-llm-in-build-rules-system-prompt-e-4dd9)
- Start cleanly with an empty accounts list: `_select_account` now serves requests account-less when zero accounts are configured instead of raising `ConfigurationError` on every request (including `/health`). (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260723T125240Z-start-cleanly-with-an-empty-accounts-lis-7691)
- Thread `api_key` and `provider_model` through `_check_unsubscribe_for_to_delete` to `_detect_unsubscribe_for_sender`, so that unsubscribe detection uses the explicitly-provided credentials instead of silently falling back to config-level resolution. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260723T133742Z-thread-api-key-unsubscribe)
- Wire `detector_level` and `draft_level` config fields into their
  respective LLM calls so they no longer silently fall back to tier 1.
  Both fields were already defined and schema-validated but the resolved
  level was discarded at the call site.  `classifier_level` was already
  correctly wired in a prior change. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260804T155001Z-wire-configured-llm-levels-classifier-le-8021)
- Fix crash when adding the first account to a fresh-deploy config with ``accounts=[]`` and ``default_account_id=''`` — the handler now falls back to the first account's id when the existing default is empty, and wraps ``MailAccountsConfig`` construction in try/except so validation errors render the form with an error message instead of crashing the request handler. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260731T155400Z-add-account-crashes-with-configurationer-60a4)
- Add field_validator for method on UnsubscribeDetection model to reject unrecognised values (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260705T185306Z-add-method-field-validator-to-unsubscribe-detection)
- Removed two shipped placeholder mail accounts ("Personal Gmail" and "Work Mailbox") from ``config/config.json`` so fresh deploys start with zero configured accounts. ``MailAccountsConfig`` now allows an empty account list, ``_cmd_serve`` boots with an in-memory DB when no accounts are present, and ``_reconcile_loop`` gracefully idles and re-checks on each cycle for newly added accounts. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260728T212324Z-remove-shipped-placeholder-mail-accounts-e8b2)
- Fix unsubscribe link failing with Bad Request when ``List-Unsubscribe`` header contains multiple comma-separated URIs. The header is now parsed per RFC 2369 to extract a single preferred URI (https/http over mailto). (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T215344Z-unsubscribe-link-fails-with-bad-request-b62c)
- Bump the `uv` binary used by the container build from 0.5.11 to 0.12.1. `uv.lock` is now written at `revision = 3`, which 0.5.11 cannot parse — the image build failed with `error: Failed to parse uv.lock` as soon as a dependency update regenerated the lock. Every other repo in the fleet already pins uv 0.11.x or newer. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/uv-lock-revision-3)

### Removals and deprecations

- Removed dead pydantic v1 compatibility `hasattr(field_info, "is_required")` guard in `_render_mailconfig_surface()`. The project requires pydantic ≥ 2.0 where `FieldInfo.is_required()` is always available. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260722T010422Z-remove-dead-pydantic-v1-compat-guard-in-939d)
- Remove `VOLUME /data` directive from the Dockerfile. The app writes
  under ``/home/mailbot`` and the anonymous volume created by this
  directive accumulated orphan volumes on every container recreate. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T082208Z-vestigial-volume-data-in-dockerfile-spaw-a319)
- Simplify `entrypoint.sh` to the robotsix inverted-entrypoint contract: strip config validation (now owned by the Python application), replace `MAIL_CONFIG_PATH` with `ROBOTSIX_CONFIG_FILE`, and keep only genuine startup work (envsubst templating). (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T082456Z-simplify-entrypoint-sh-for-post-config-m-f5e8)
- Migrate config to typed-JSON contract: removed fallback config loading in loader.py (now uses robotsix_config exclusively), replaced YAML example with multi-account JSON example, and updated CI config-sync checker and advisory agent to validate against JSON instead of YAML. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260721T091444Z-migrate-robotsix-auto-mail-to-the-typed-a5fe)
- Deploy contract: central-deploy no longer manages the app config. The `robotsix.deploy.config-target` / `config-assist` / `config-assist-seeds` labels are removed (central-deploy writes YAML, the app reads only JSON at `ROBOTSIX_CONFIG_FILE`); `config.json` is seeded manually into the config volume. The board service now binds `0.0.0.0` in the deploy compose so the gateway can reach it, and the local compose points at `config/config.json` instead of the removed `mail.local.yaml`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T100000Z-deploy-contract-json-only-config)
- Drop upper bound on requires-python, remove file logging (log_file_dir / .mail_log), and remove bandit from pre-commit hooks. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T101218Z-standards-round-2-drop-requires-python-u-a525)
- Remove the central-deploy managed-config artifacts: the committed `config/config.yaml` template, `config/config.schema.json`, and the CI schema-drift check. The presence of the schema made central-deploy's onboarding preflight demand a `config-target` label; config is now seeded manually as `config.json` and central-deploy config management is fully opted out of. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T103000Z-drop-managed-config-artifacts)
- Consolidate "Add Account" UI entry points: the board header button now opens the settings add-account form, the standalone link and always-visible inline form are removed. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260807T123426Z-consolidate-add-account-entry-points-kee-1e00)
- Removed unused `set_component_setting` function from `db/queries.py` and its re-export from `db/__init__.py`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260731T134124Z-remove-dead-set-component-setting-single-116d)
- Removed orphaned `_VALID_CONFIDENCE_LEVELS` re-export from `triage._constants` (was imported but never used; peers route through `validate_confidence` instead). (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260806T204307Z-remove-orphaned-valid-confidence-levels-c52c)
- Removed all environment-variable overrides for LLM secrets and settings (`LLM_API_KEY`, `LLM_PROVIDER_MODEL`). The API key now resolves solely from `config.json`. The legacy plain-JSON fallback loader has been removed. A schema-drift check has been added to the CI config-sync step. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260721T205329Z-config-clean-cutover-migration-to-robots-6cda)
- The flat ``llm_provider_model`` field on ``MailAccountsConfig`` has been replaced with a tier-based ``models`` config (``level1``–``level4`` overrides) and per-application level fields (``triage_level``, ``classifier_level``, ``rules_level``, ``detector_level``, ``draft_level``). Blank overrides resolve to the llmio tier default, and each task selects its tier via its configured application level. Level 4 is wired through. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T213409Z-replace-flat-llm-provider-model-with-llm-afde)
- Removed ``default_account_id`` setting and all single-account-fallback code paths from the config model, CLI, and HTTP handlers.  The account a request/CLI command operates on is now mandatory and explicit; the initial board view defaults to the first account in configured order.  CLI ``--account`` is now required on ``board``, ``probe``, ``triage``, and ``config-sync`` subcommands. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T214131Z-remove-default-account-and-single-accoun-41f0)

### Miscellaneous

- Register PR #968's changelog fragment in docs/modules.yaml (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260725T000213Z-register-pr-968-s-changelog-fragment-in-ec0a)
- Cleanup duplicate path in module core: changelog fragment listed twice in docs/modules.yaml (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260721T001055Z-cleanup-duplicate-path-in-module-core-ch-3ccf)
- robotsix-auto-mail: Enable repo_description_sync periodic workflow (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260718T002044Z-robotsix-auto-mail-enable-repo-descripti-f04d)
- Introduce a RobotsixMailError base and reparent the 9 domain exceptions (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260721T002604Z-introduce-a-robotsixmailerror-base-and-r-a8c1)
- ci_fix: out-of-scope CI failure — Coverage comment (python-coverage-comment-action HTTP 503 from GitHub API) in .github/workflows/ci.yml — the coverage-comment job needs retry logic for the python-coverage-comment-action step to handle transient GitHub API errors. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260720T002929Z-ci-fix-out-of-scope-ci-failure-coverage-a8c0)
- robotsix-auto-mail: migrate command: override and BOARD_PORT out of docker-compose.yml (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260727T003529Z-robotsix-auto-mail-migrate-command-overr-fef7)
- ci_fix: out-of-scope CI failure — Run pre-commit hooks / validate-pyproject in .pre-commit-config.yaml or pyproject.toml — the validate-pyproject hook version may need pinning/updating, or pyproject.toml may need adjustment for the hook's uv-schema validator (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260721T004554Z-ci-fix-out-of-scope-ci-failure-run-pre-c-aaa3)
- Add towncrier build step to release.yml so versioned CHANGELOG sections are compiled before release notes extraction (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260722T004846Z-add-towncrier-build-step-to-release-yml-c4f8)
- Replace duplicate config-key table in docs/connecting.md with a link to docs/configuration.md (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T005011Z-replace-duplicate-config-key-table-in-do-8d05)
- Update docs/architecture.md to reflect current module layout (triage/rules.py, pipeline/_parse.py) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T005011Z-update-docs-architecture-md-to-reflect-c-66b4)
- Fix stale CHANGELOG entry claiming allowed_origins config field was added (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260731T010315Z-fix-stale-changelog-entry-claiming-allow-d909)
- Remove stale CHANGELOG.md entry that claims robotsix-http dependency (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260724T010820Z-remove-stale-changelog-md-entry-that-cla-bec6)
- ci_fix: out-of-scope CI failure — validate-pyproject (pre-commit hook) in .pre-commit-config.yaml or pyproject.toml (to fix or suppress the validate-pyproject hook bug, which is unrelated to this PR) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260721T011535Z-ci-fix-out-of-scope-ci-failure-validate-5dce)
- Centralized watermark sentinel values ``"running"`` and ``"idle"`` into shared constants ``_WATERMARK_RUNNING`` and ``_WATERMARK_IDLE`` in ``core/_constants.py``, replacing bare string literals across 6 source files. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260801T011548Z-centralize-watermark-state-sentinel-magi-fa60)
- Removed a stale `per-file-ignores` entry for the non-existent `src/robotsix_auto_mail/detect.py` and updated the associated deptry comment to reference the actual lazy-import sites. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260801T011548Z-remove-orphaned-ruff-per-file-ignore-and-4fab)
- Clear repo-wide ruff lint debt (remove unused `# noqa: S310` directives). (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T012725Z-clear-repo-wide-ruff-debt-fix-module-reg-af3d)
- Consume coverage artifact from reusable CI job instead of re-running the full test suite in coverage-comment (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260723T013124Z-consume-coverage-artifact-from-reusable-4f5d)
- Config schema: make account 'password' optional so service can deploy without credentials (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260725T013433Z-config-schema-make-account-password-opti-19ac)
- Extracted the duplicated handler-factory cache-update idiom from `_account_mixin.py` and `_settings_mixin.py` into a shared `_update_handler_factory_cache` helper in `server/_constants.py`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T013606Z-extract-handler-factory-cache-update-int-92b3)
- Strengthen AGENT.md changelog-fragment registration rule: explicit "applies to ALL PRs" note and mandatory ``git grep`` self-check before implement-stage completion. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260731T014311Z-enforce-changelog-fragment-registration-self-check)
- Register changelog fragment 20260730T181056Z-split-tests-pipeline-test-parser-py-508 in docs/modules.yaml (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260731T014312Z-register-changelog-fragment-20260730t181-9e17)
- Wrap sys.path.insert in try/finally in config_sync_agent.py to prevent permanent global state mutation (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260804T014410Z-wrap-sys-path-insert-in-try-finally-in-c-980d)
- Sanitize HTTP error responses to prevent exception message leakage in _action_mixin.py and _triage_mixin.py (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260804T014412Z-sanitize-http-error-responses-to-prevent-4d91)
- Centralised the five LLM application-name strings into ``APP_*`` constants in ``config._constants`` and added a validation guard to ``resolve_application_level()``. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260805T015416Z-centralize-llm-application-name-magic-st-b229)
- Add dedicated unit tests for ConfigVersionStore in config/versions.py (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260806T020517Z-add-dedicated-unit-tests-for-configversi-6d2b)
- Add unit tests for SettingsStore and /settings endpoints (settings store CRUD, secret masking, field validation, GET/POST handler tests). (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260731T021749Z-add-unit-tests-for-the-per-component-set-46c7)
- Updated board URL comment in docker-compose.yml to use fixed port 8080 instead of ${BOARD_PORT:-8080}. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260727T021927Z-replace-board-port-comment.f754)
- Extract inline CSS from `_account_mixin.py` and `_settings_mixin.py` into dedicated `.css` files under `static/`, loaded at module level via `Path.read_text()`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260807T021948Z-extract-inline-css-from-server-mixin-fil-0498)
- Narrow exception types in _probe_capabilities to eliminate CodeQL py/empty-except (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260719T022116Z-narrow-exception-types-in-probe-capabili-8518)
- robotsix-auto-mail: Remove dead security_posture.yaml periodic presence file (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260725T022916Z-robotsix-auto-mail-remove-dead-security-eee3)
- register the new changelog fragment in docs/modules.yaml (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260731T023847Z-register-the-new-changelog-fragment-in-d-fa86)
- Update stale ingest contract in docs/deployment.md to match docker-compose's config-driven command: ingest (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260731T024237Z-update-stale-ingest-contract-in-docs-dep-423b)
- CodeQL inline lgtm suppressions and query-filters config not respected in PR checks (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260719T024350Z-codeql-inline-lgtm-suppressions-and-quer-2c10)
- Removed dead `_patch_serve_board_deps` autouse fixture and renamed
  `tmp_db_path` to `_fake_db_path` in `tests/server/_view_mixin_helpers.py`
  to eliminate collision with root conftest. Deleted empty `tests/db/conftest.py`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260803T024649Z-remove-dead-test-fixtures-and-fix-tmp-db-1cbe)
- Resolve pre-existing CI failures: upgrade cryptography transitive dependency to 50.0.0, fix CodeQL py/log-injection and py/unused-global-variable alerts. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260805T024848Z-ci-fix-out-of-scope-ci-failure-repositor-ee1c)
- Replace `pip install` instructions with `uv`-native equivalents in developer setup docs. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260724T025143Z-replace-pip-install-instructions-in-deve-a388)
- Fix startup_failure on CI and Release image workflows on main (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260725T030052Z-fix-startup-failure-on-ci-and-release-im-96b3)
- Restore `# noqa: S110` on bare `except Exception: pass` clauses OR narrow the exceptions properly (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260719T030252Z-restore-noqa-s110-on-bare-except-excepti-be96)
- Migrate robotsix-auto-mail to consume robotsix-http (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260724T031442Z-migrate-robotsix-auto-mail-to-consume-ro-6a1e)
- Add CSS custom properties layer to board.css to eliminate 51 hardcoded hex colors (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T032222Z-add-css-custom-properties-layer-to-board-12dc)
- Add stylelint-declaration-strict-value to pre-commit to gate new hardcoded CSS colors (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T032222Z-add-stylelint-declaration-strict-value-t-65d4)
- Extract duplicated _launch_background_worker call into _launch_triage helper in _triage_mixin.py (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T032222Z-extract-duplicated-launch-background-wor-61ca)
- Extract shared _force_refresh method to eliminate IMAP/SMTP token-refresh duplication (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T032222Z-extract-shared-force-refresh-method-to-e-6b6e)
- Split 195-line card_extra_html method in board_adapter.py into widget helper functions (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T032222Z-split-195-line-card-extra-html-method-in-2cb0)
- Add codeql.yml using shared reusable workflow to robotsix-auto-mail (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T035854Z-add-codeql-yml-using-shared-reusable-wor-d275)
- Add lint-workflows.yml using shared reusable to robotsix-auto-mail (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T035854Z-add-lint-workflows-yml-using-shared-reus-1edb)
- Added `pytest-randomly` as a dev dependency and configured `--randomly-seed=last` in CI's pytest invocation. This randomizes test execution order on each run to surface hidden ordering dependencies — tests that pass only because a previous test set up global state, monkeypatching, or DB state they implicitly depend on. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260727T040044Z-add-pytest-randomly-to-dev-dependencies-c908)
- Split ``tests/server/test_batch_mixin.py`` into four focused modules:
  ``test_batch_mixin_delete.py``, ``test_batch_mixin_delete_aggregate.py``,
  ``test_batch_mixin_archive_folder.py``, and ``test_batch_mixin_archive.py``.
  Extracted the shared ``_BatchFakeHandler`` to ``tests/server/_test_helpers.py``
  alongside the existing ``_DraftMixinFakeHandler``. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260727T040044Z-split-tests-server-test-batch-mixin-py-5-5d2f)
- Split tests/server/test_draft_mixin_send_generate.py (731 lines) into per-class test modules (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260727T040044Z-split-tests-server-test-draft-mixin-send-0733)
- Remove orphaned `[tool.bandit]` config section from pyproject.toml (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260728T040337Z-remove-orphaned-tool-bandit-config-secti-b295)
- Add `parallel = true` to `[tool.coverage.run]` in pyproject.toml (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260725T040523Z-add-parallel-true-to-tool-coverage-run-i-0524)
- robotsix-auto-mail: Enable completeness_check periodic workflow (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260716T040733Z-robotsix-auto-mail-enable-completeness-c-3fac)
- Register 7 unregistered changelog fragments in core module paths (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260725T040918Z-classify-7-unregistered-changelog-fragme-0e60)
- Register PR #973's changelog fragment in docs/modules.yaml (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260725T041822Z-register-pr-973-s-changelog-fragment-in-b0fb)
- Missing re-export: `normalize_archive_subfolder` not exposed through `triage/__init__.py` (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260717T044305Z-missing-re-export-normalize-archive-subf-33d3)
- Missing re-export: `ParseError` and `parse_message` not exposed through `pipeline/__init__.py` (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260717T044305Z-missing-re-export-parseerror-and-parse-m-b662)
- Missing re-export: `DEFAULT_INGEST_INTERVAL_MINUTES` not exposed through `config/__init__.py` (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260718T044535Z-missing-re-export-default-ingest-interva-1768)
- Add missing re-export of ``DEFAULT_RULES_HEADER`` in ``robotsix_auto_mail.triage`` (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260728T045028Z-missing-re-export-default-rules-header-f-7d42)
- Missing re-export: `get_account_health` and `write_account_health` not exposed through `db/__init__.py` (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260716T045127Z-missing-re-export-get-account-health-and-70a6)
- Missing re-export: `save_accounts` not exposed through `config/__init__.py` (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260720T045441Z-missing-re-export-save-accounts-not-expo-97ec)
- Update implement agent prompt (AGENT.md) to require registering new
  changelog fragments in `docs/modules.yaml` under the `core` module's
  `paths` list.  This eliminates a recurring source of CI noise where
  the implement agent creates a fragment but forgets to register it,
  triggering a `robotsix-modules check-registration` failure and a
  follow-up CI-fix commit. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T050008Z-implement-agent-auto-register-changelog-dad5)
- config drift: stale key `component_agent_enabled` in config.example.json (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T051132Z-config-drift-stale-key-component-agent-e-4d53)
- Remove stale `component_agent_enabled` key from `config/config.example.json` — the feature was removed upstream but the config example wasn't cleaned up. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T051132Z-remove-stale-component-agent-enabled-config-example-4d53)
- ci_fix: out-of-scope CI failure — lint-workflows / zizmor (33 findings in pre-existing files) in Multiple pre-existing workflow files: ci.yml, codeql.yml, lockfile.yml, release.yml, scan-container.yml, docs.yml, dependabot-auto-merge.yml, pre-commit.yml (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T060804Z-ci-fix-out-of-scope-ci-failure-lint-work-34df)
- ci_fix: out-of-scope CI failure — zizmor / excessive-permissions (lockfile.yml) and CodeQL py/ineffectual-statement (vulture_whitelist.py) in .github/workflows/lockfile.yml (move contents: write from workflow level to job level) and vulture_whitelist.py:106 (fix ineffectual statement) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T070912Z-ci-fix-out-of-scope-ci-failure-zizmor-ex-78e5)
- ci_fix: out-of-scope CI failure — lint-workflows / zizmor (excessive-permissions) in .github/workflows/codeql.yml, .github/workflows/lockfile.yml, and .github/workflows/scan-container.yml — each needs permissions tightened to job level or a minimum permissions block added. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T073043Z-ci-fix-out-of-scope-ci-failure-lint-work-d14b)
- Split `tests/cli/test_cli_detect_accounts.py` (558 lines) into four thematic modules: `test_cli_detect_report.py`, `test_cli_detect_accounts.py`, `test_cli_detect_flags.py`, and `test_cli_detect_interactive.py`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260726T074052Z-split-tests-cli-test-cli-detect-accounts-efb2)
- ci: skip Python-specific jobs when no .py files change (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T074502Z-ci-skip-python-specific-jobs-when-no-py-8cb9)
- ingester: stay healthy (idle standby) when zero accounts are configured (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260726T075146Z-ingester-stay-healthy-idle-standby-when-bc58)
- Remove stale `SenderMemory` and `ArchiveFolderMemory` entries from `vulture_whitelist.py` — both classes were removed from `src/robotsix_auto_mail/triage/persistence.py` in a prior refactor. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260720T081446Z-remove-stale-sendermemory-and-archivefol-dc24)
- GET /healthz returns 404 — container permanently unhealthy since the 2026-07-04 update (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T082208Z-get-healthz-returns-404-container-perman-4432)
- Adopt towncrier for changelog management (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T082456Z-adopt-towncrier-for-changelog-management-a50b)
- Mailbox shows 0 mails for configured accounts — mail fetch/display broken (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260801T082711Z-mailbox-shows-0-mails-for-configured-acc-247a)
- CI failure: uv in /. - Update #1446419417 on main (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T085156Z-ci-failure-uv-in-update-1446419417-on-ma-171e)
- Remove unregistered duplicate changelog fragment 20260731T123451Z-test-gap-add-unit-tests-for-src-robotsix-333e.misc.md (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260801T085322Z-remove-unregistered-duplicate-changelog-5323)
- Extract shared plain-socket helper from _connect_plain and _connect_starttls in imap/client.py (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260705T091322Z-extract-shared-plain-socket-helper-from-5d24)
- Extracted shared OAuth2 setup logic from ``ImapClient`` and ``SmtpClient``
  constructors into the ``_ProtocolClient`` base class, removing 12 lines of
  duplicate boilerplate. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260705T091322Z-factor-out-shared-oauth2-constructor-boi-de1a)
- Split `tests/cli/test_commands_serve.py` into two modules: the reconcile-loop tests move to `test_commands_serve_reconcile.py` and the local `_accounts()` helper is replaced by the shared conftest import. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260730T091340Z-split-tests-cli-test-commands-serve-py-5-68b8)
- Update stale `mail.local.yaml` references across docs and AGENT.md after JSON-only config migration (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260730T091340Z-update-stale-mail-local-yaml-references-e6cc)
- ci_fix: out-of-scope CI failure — repo-checks (towncrier check shallow clone + zizmor/artipacked) in .github/workflows/ci.yml (add fetch-depth: 0 to the repo-checks checkout step); .github/workflows/pre-commit-autoupdate.yml (add persist-credentials: false to the checkout step) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T092619Z-ci-fix-out-of-scope-ci-failure-repo-chec-e128)
- Deactivate all periodic mill workflows (keep none) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260706T093140Z-deactivate-all-periodic-mill-workflows-k-4789)
- Consolidate near-identical single-column UPDATE functions in db/queries.py into a shared helper (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260706T100734Z-consolidate-near-identical-single-column-749d)
- Consolidate five near-identical single-column UPDATE functions in ``db.queries`` into a shared ``_update_column`` helper, reducing ~100 lines of copy-paste to ~32 while preserving the public API. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260706T100734Z-consolidate-update-column-queries-749d)
- Extracted 25 private helper functions and 2 utility classes from
  ``tests/server/conftest.py`` into a new ``tests/server/conftest_helpers.py``
  module.  ``conftest.py`` now contains only the 5 ``@pytest.fixture``
  definitions (~90 lines), importing the helpers it needs from the new module. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260706T100734Z-extract-private-helpers-from-tests-serve-10f9)
- Split server/views/board.py — extract data-loading logic into board_data.py (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260706T100734Z-split-server-views-board-py-extract-data-6558)
- Split tests/server/test_server_archive_delete.py (790 lines) into per-theme test modules (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260729T103125Z-split-tests-server-test-server-archive-d-aa66)
- CI workflow on main fails at startup (zero jobs) — fresh fix against clean main (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T103200Z-ci-workflow-on-main-fails-at-startup-zer-6a4e)
- "Detect Settings" on Add Mail Account requires host fields to already be filled — defeats its purpose (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260729T105615Z-detect-settings-on-add-mail-account-requ-a7ee)
- Drop pre-commit-autoupdate.yml (redundant with dependabot pre-commit ecosystem) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T110407Z-drop-pre-commit-autoupdate-yml-redundant-a748)
- Add `permissions: contents: read` to the `sbom` caller job in `.github/workflows/ci.yml` to satisfy the reusable `sbom.yml` workflow's permission requirement, preventing GitHub from rejecting the workflow at startup. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T112803Z-land-the-verified-ci-startup-fix-on-main-19c4)
- Remove broker/component-agent integration; rename /healthz→/health (liveness); adopt standard image layout (user app, /home/app, /data) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T114033Z-remove-broker-integration-rename-healthz-5ad9)
- robotsix-auto-mail: Enable agent_check periodic workflow (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260720T114054Z-robotsix-auto-mail-enable-agent-check-pe-d9d1)
- Update AGENT.md Configuration conventions for typed-JSON config contract (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260721T122323Z-update-agent-md-configuration-convention-b9ac)
- Replace duplicated IMAP archive-move logic in _archive_and_delete cross-folder fallback (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260722T122628Z-replace-duplicated-imap-archive-move-log-50af)
- Add-account page returns 'Forbidden: cross-origin request rejected' (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260729T123424Z-add-account-page-returns-forbidden-cross-2ab9)
- Added dedicated unit tests for `_ReconcileMixin._handle_reconcile` covering idempotency guard, per-account thread spawning in aggregate mode, and single-thread non-aggregate path. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260731T123451Z-add-unit-tests-for-reconcile-mixin-333e)
- test gap: add unit tests for src/robotsix_auto_mail/settings/import_.py (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260731T123451Z-test-gap-add-unit-tests-for-src-robotsix-ce3f)
- Remove accidentally committed empty test-file (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260804T123919Z-remove-accidentally-committed-empty-test-b9b1)
- test gap: add unit tests for src/robotsix_auto_mail/server/_settings_mixin.py (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260801T125239Z-test-gap-add-unit-tests-for-src-robotsix-d249)
- Add a UI button to add a new mail account using the existing account-creation script (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260723T125250Z-add-a-ui-button-to-add-a-new-mail-accoun-2715)
- Missing re-export: `ProviderEntry` from `config/detect/models.py` not exposed through `config/detect/__init__.py` (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260730T125533Z-missing-re-export-providerentry-from-con-a26d)
- Remove the spurious `changelog.d/*.md` glob from the `core` module's `paths` list in `docs/modules.yaml` (the `changelog.d/` directory does not exist; only `changelog/` does). (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260731T131538Z-remove-spurious-changelog-d-md-glob-from-0075)
- Fix all remaining ruff lint and format violations across the whole repository, and register all unregistered changelog fragments and test files in ``docs/modules.yaml`` so the module-registration completeness check passes on CI. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260801T132605Z-clean-main-part-2-fix-whole-repo-ruff-li-d0f7)
- Thread resolved API key / provider-model from `run_triage_agent` into `_check_unsubscribe_for_to_delete` (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260723T133742Z-thread-resolved-api-key-provider-model-f-1dfa)
- agent_limitation — The CI-fix agent spent 3,538s (59 min) and 98 tool calls fixing langfuse test-initialization drift across 3 test files (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260803T134250Z-agent-limitation-the-ci-fix-agent-spent-4d48)
- mail-ingester container restart-loops: starts without a valid subcommand, prints CLI usage and exits (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260730T135940Z-mail-ingester-container-restart-loops-st-e3ce)
- Added `.robotsix-mill/periodic/triage_boilerplate.yaml` periodic workflow config. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260720T141755Z-enable-triage-boilerplate-periodic-workf-c6b4)
- Enable module_size periodic workflow to scan for oversized Python modules
  and propose split tickets. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260721T142307Z-robotsix-auto-mail-enable-module-size-pe-4287)
- Adopt internal per-component settings (migrate config off central-deploy) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260730T142338Z-adopt-internal-per-component-settings-mi-59ae)
- Add dedicated unit tests for ``_ConfigMixin`` covering all branches of ``_handle_config_sync`` and ``_handle_archive_proposal``. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T142404Z-test-gap-add-unit-tests-for-src-robotsix-1006)
- Remove orphaned cli.config file-write helper cluster (_existing_account_ids, _existing_accounts_for_append, _find_existing_account, _load_accounts_from_file) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260801T142634Z-remove-orphaned-cli-config-file-write-he-1a0b)
- Remove or register the unregistered duplicate changelog fragment 20260731T014311Z-enforce-implement-stage-changelog-fragme-e4cc.misc.md from main (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260731T143240Z-remove-or-register-the-unregistered-dupl-746a)
- Add env var resolution to resolve_llm_api_key and resolve_llm_provider_model (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T144458Z-add-env-var-resolution-to-resolve-llm-ap-0c9b)
- Add concurrency group to lint-workflows.yml to prevent redundant CI runs (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260727T150011Z-add-concurrency-group-to-lint-workflows-3e61)
- Add step-security/harden-runner to all CI workflows for supply-chain hardening (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260727T150011Z-add-step-security-harden-runner-to-all-c-a032)
- Split `tests/server/test_account_mixin.py` (562 lines, 37 tests, 4 classes)
  into four per-class modules and extract shared helpers (`_AccountMixinFakeHandler`,
  `_make_post_body`) to `tests/server/_test_helpers.py`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260727T150011Z-split-tests-server-test-account-mixin-py-5f27)
- Consolidate duplicated CSS property blocks in board.css into a shared class (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T150328Z-consolidate-duplicated-css-property-bloc-b341)
- Extend mypy strict checking to cover test files with relaxed overrides (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T150328Z-extend-mypy-strict-checking-to-cover-tes-7bf1)
- Remove unregistered duplicate changelog fragment 20260731T182528Z-clean-main-fix-ruff-lint-format-violatio-2581.misc.md from main (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260801T151011Z-remove-unregistered-duplicate-changelog-4fd8)
- ci_fix: out-of-scope CI failure — shellcheck SC2329 in scripts/server/smoke_board.sh (unused cleanup() function) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T151937Z-ci-fix-out-of-scope-ci-failure-shellchec-fbb2)
- Refactored `_run_batch_delete_background` and `_run_batch_archive_background` into thin wrappers around a new shared `_run_batch_background` parameterised driver, removing ~150 lines of duplicated IMAP orchestration code. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T152130Z-refactor-mirrored-batch-workers-in-serve-cf18)
- Enable baseline periodic mill agents: `test_gap`, `security_posture`, and `module_curator`, via minimal presence files in `.robotsix-mill/periodic/`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260714T153350Z-robotsix-auto-mail-enable-baseline-perio-47b4)
- Add triage boilerplate pattern for completeness_check missing re-export tickets (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260720T153746Z-boilerplate-missing-re-export-ticket-tri-c9c3)
- Added scope-triage boilerplate for CHANGELOG.md, docs/modules.yaml, and changelog/*.md accompanying-documentation patterns. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260720T153746Z-scope-triage-accompanying-docs-boilerplate)
- robotsix-auto-mail: Enable changelog_autofill periodic workflow (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260719T154209Z-robotsix-auto-mail-enable-changelog-auto-acb6)
- Remove unregistered duplicate changelog fragment (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260720T160231Z-remove-unregistered-duplicate-changelog-a908)
- CI failure: uv in / for zizmor - Update #1479629976 on main (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260724T161520Z-ci-failure-uv-in-for-zizmor-update-14796-30f7)
- CI failure: Release image on main (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260724T161547Z-ci-failure-release-image-on-main-e1da, https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260724T180805Z-ci-failure-release-image-on-main-5747, https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260717T210820Z-ci-failure-release-image-on-main-ed22, https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260725T222235Z-ci-failure-release-image-on-main-6ca7)
- CI failure: CI on main (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260724T161646Z-ci-failure-ci-on-main-32c0)
- test gap: add unit tests for src/robotsix_auto_mail/server/_triage_mixin.py (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T162148Z-test-gap-add-unit-tests-for-src-robotsix-8ead)
- test gap: add unit tests for src/robotsix_auto_mail/server/_component_agent_mixin.py (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T162148Z-test-gap-add-unit-tests-for-src-robotsix-d241)
- test gap: add unit tests for src/robotsix_auto_mail/server/_batch_mixin.py (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T162148Z-test-gap-add-unit-tests-for-src-robotsix-d2d8)
- Split ``tests/server/test_server_draft.py`` (592 lines) into two focused modules: ``test_server_draft_manage.py`` (draft move/save/generate) and ``test_server_draft_send.py`` (send-draft reply/forward/validation/buttons). Extracted the shared ``_patch_smtp_and_imap`` context manager into ``tests/server/_draft_helpers.py``. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260728T162916Z-split-tests-server-test-server-draft-py-3fdc)
- Wire the calendar write path: `TO_CALENDAR` action has no dispatch branch in `move_action`, leaving `update_calendar_event_ref`/`update_calendar_correlation_id` (re-exported, documented as the column writers) with zero callers so the read surfaces render empty (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260805T163702Z-wire-the-calendar-write-path-to-calendar-b027)
- Reorganize `mime` module into a per-module package layout (`src/robotsix_auto_mail/mime/__init__.py`) with no import or API breakage. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260714T165938Z-reorganize-module-mime-align-to-per-modu-4fc4)
- Removed spurious `changelog.d/*.md` glob from the `core` module in `docs/modules.yaml`. No `changelog.d/` directory exists in the repo; changelog fragments are already covered by `changelog/*.md`. Updated stale comment in `tests/conftest.py` to reference the new merge tests in `tests/settings/test_discover_accounts.py`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260801T171822Z-add-positive-path-tests-for-discover-acc-cc8b)
- Added testing convention to AGENT.md: novel functions in fix/recovery paths must have positive-path unit tests exercising the real function body, mocking at dependencies, not the function itself. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260801T171831Z-agent-md-testing-conventions-every-novel-b56c)
- Derive package version from git tags via hatch-vcs so `--version` and `__version__` track the release tag instead of reporting a hardcoded `0.0.0`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T175030Z-sync-package-version-to-the-git-release-56b1)
- Fixed out-of-scope CI failures: suppressed CodeQL false positives (py/unused-global-variable on static asset constants, py/log-injection on account deletion logging), removed unused local variable in _cmd_serve, and bumped transitive cryptography dependency to >=50.0.0 to address CVE-2026-69247. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260804T175142Z-ci-fix-out-of-scope-ci-failure-uv-audit-cd15)
- Register 20260724T161520Z changelog fragment in docs/modules.yaml (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260724T175523Z-register-20260724t161520z-changelog-frag-a8d6)
- ci_fix: out-of-scope CI failure — Python CI / tests (ruff check), Repository checks (uv audit), CodeQL py/mixed-returns in 26 Python source/test files with ruff violations (pre-existing), pymdown-extensions dependency upgrade (pre-existing), src/robotsix_auto_mail/cli/commands.py CodeQL (pre-existing) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260731T175636Z-ci-fix-out-of-scope-ci-failure-python-ci-851f)
- Remove stray empty file tests/cli/test_cli_config_sync_test.tmp (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260807T180217Z-remove-stray-empty-file-tests-cli-test-c-c5c2)
- Migrate robotsix-auto-mail to use shared scan-container.yml from robotsix-github-workflows (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T180457Z-migrate-robotsix-auto-mail-to-use-shared-28fc)
- Split tests/pipeline/test_parser.py (508 lines, 30 tests) into per-theme test modules (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260730T181056Z-split-tests-pipeline-test-parser-py-508-0ab6)
- Fix pre-existing ruff format violations and CodeQL py/mixed-returns issues; pin uv to 0.12.1 in the setup action and use `UV_PREVIEW=1` for the `uv audit` CI step for broader uv-version compatibility. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260731T182528Z-fix-ruff-lint-format-and-uv-audit-ci-2581)
- Board: adopt robotsix-board's move-control removal. The library no longer renders its own `<form class="board-card-move">` per card, so auto-mail's CSS suppression of it (`.board-card > form.board-card-move { display: none }`) is gone along with the redundant markup — the only move form on the page is now auto-mail's own triage-action form, injected through `card_extra_html`. `MailBoardAdapter.move_endpoint()` stays as auto-mail's own helper (it feeds `_move_form`) but is no longer a `BoardAdapter` Protocol member; `move_endpoint_template()` had no caller left and is removed. Also aligns the `robotsix-modules` pin with robotsix-board's, which the bump turned into a hard `uv lock` conflict. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T183000Z-drop-library-move-form)
- Removed duplicate `conn` fixture in `tests/settings/test_store.py` and consolidated `single_db` to delegate to root `tmp_db_path`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260803T183346Z-deduplicate-the-conn-sqlite-test-fixture-f344)
- test gap: add unit tests for src/robotsix_auto_mail/config/model.py (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260717T183958Z-test-gap-add-unit-tests-for-src-robotsix-47ec)
- Enable `docstring_coverage` periodic agent to scan for undocumented public API and propose draft tickets. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260718T184436Z-robotsix-auto-mail-enable-docstring-cove-d6c6)
- Enable `health` periodic agent to inspect the repository and file draft tickets for gaps. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260718T184436Z-robotsix-auto-mail-enable-health-periodi-fbdb)
- Register three additional unregistered changelog fragments from PRs #959, #960, #961 in docs/modules.yaml (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260724T185005Z-register-three-additional-unregistered-c-4451)
- config-sync agent: `_load_ledger` now gracefully handles corrupt watermark JSON and skips malformed ledger entries instead of crashing (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260705T185305Z-config-sync-load-ledger-graceful-json-error-handling-9848)
- config_sync: `_load_ledger` missing JSON-decode error handling (crash on corrupted watermark) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260705T185305Z-config-sync-load-ledger-missing-json-dec-9848)
- UnsubscribeDetection.method field missing validator — LLM prompt constraint not enforced by model (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260705T185306Z-unsubscribedetection-method-field-missin-51c9)
- Make robotsix-auto-mail compliant with the robotsix config standard (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260721T185654Z-make-robotsix-auto-mail-compliant-with-t-38da)
- Move `scripts/vendor-ui.sh` to `scripts/server/vendor-ui.sh` to align with the per-module scripts layout convention; update the internal `DEST` path to account for the deeper nesting. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260806T190505Z-reorganize-module-server-align-to-per-mo-a6a0)
- Consolidate modules config, detect: move detect under config as config/detect/ (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260715T191710Z-consolidate-modules-config-detect-move-d-9178)
- Reorganized the `core` module: moved `_constants.py`, `_llm_agent.py`, `_observability.py`, `format.py`, and `health.py` from the package root into a new `src/robotsix_auto_mail/core/` sub-package, aligning with the per-module directory convention used by all other modules. All imports updated accordingly across 32 files. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260715T191710Z-reorganize-module-core-align-to-per-modu-8b73)
- robotsix-auto-mail: Enable audit periodic workflow (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260716T192336Z-robotsix-auto-mail-enable-audit-periodic-bbeb)
- robotsix-auto-mail: Enable copy_paste periodic workflow (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260716T192337Z-robotsix-auto-mail-enable-copy-paste-per-3ddd)
- Drop the dead `-m "not docker"` pytest filter from CI (no `docker` marker exists or is registered) and enable `--strict-markers` so unregistered markers fail instead of silently selecting nothing. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260806T192424Z-register-the-phantom-docker-test-marker-b23f)
- robotsix-auto-mail: Enable bc_check periodic workflow (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260715T192538Z-robotsix-auto-mail-enable-bc-check-perio-186f)
- Docs reference obsolete `MAIL_CONFIG_PATH` env var; code uses `ROBOTSIX_CONFIG_FILE` instead (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T192757Z-docs-reference-obsolete-mail-config-path-ac4c)
- `robotsix-autoupdate` CLI entry point removed from pyproject.toml but shell script still calls it (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T192757Z-robotsix-autoupdate-cli-entry-point-remo-6151)
- copy-paste: 32-line clone in check_config_sync.py — extract shared YAML-doc validation helper (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T193857Z-copy-paste-32-line-clone-in-check-config-ce23)
- Add `level` parameter to `propose_archive_subfolder_llm` and thread from call sites (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260803T194216Z-add-level-parameter-to-propose-archive-s-718d)
- Thread configured detector_level through the detect flow (commands_detect → _detect_settings → detect_provider) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260803T194216Z-thread-level-through-the-detect-flow-com-47b4)
- Add docstring to ``main()`` entry point in ``src/robotsix_auto_mail/dev/autoupdate.py``. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260718T194949Z-docstring-gap-add-docstring-to-main-in-d-2c4a)
- Add docstring to `register_subparser` in `commands_detect.py`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260718T194950Z-docstring-gap-add-docstring-to-register-1642)
- docstring gap: add docstring to register_subparser() in cli/commands_ingest.py:22 (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260718T194951Z-docstring-gap-add-docstring-to-register-0c44)
- docstring gap: add docstring to register_subparser() in cli/commands_config_sync.py:13 (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260718T194951Z-docstring-gap-add-docstring-to-register-69fb)
- docstring gap: add docstring to register_subparser() in cli/commands_triage.py:19 (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260718T194951Z-docstring-gap-add-docstring-to-register-7a9c)
- Added 25 direct unit tests for `commands_detect.py` handler functions (`_build_detect_report`, `_cmd_detect`, `_probe_capabilities`, `_print_detect_report`). (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260720T195427Z-test-gap-add-unit-tests-for-src-robotsix-54f0)
- test gap: add unit tests for src/robotsix_auto_mail/cli/commands_config_sync.py (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260720T195429Z-test-gap-add-unit-tests-for-src-robotsix-66c0)
- Remove observability deprecation shim (robotsix_auto_mail.observability → _observability) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T195609Z-remove-observability-deprecation-shim-ro-a0f0)
- Stop a failed coverage PR comment from failing the whole CI workflow: the retry
  step now carries `continue-on-error` like the first attempt. Posting a comment
  is reporting, not a merge gate, and a persistent failure was leaving every PR's
  CI red — which mill's ci_fix answered with an endless stream of no-op commits. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T200009Z-coverage-comment-never-fails-ci)
- Enable credit_balance periodic workflow to monitor OpenRouter credit balance. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T200201Z-robotsix-auto-mail-enable-credit-balance-f959)
- Updated `docs/configuration.md` to document the tiered model configuration (`models.level1`–`level4`) and per-application level fields (`triage_level`, `classifier_level`, `rules_level`, `detector_level`, `draft_level`), replacing the removed `llm_provider_model` field. Updated `scripts/config/check_config_sync.py` to match. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260803T200359Z-update-docs-configuration-md-for-the-rem-bd2d)
- AGENT.md: Documentation conventions — When you add, remove, or rename a config field on MailAccountsConfig or MailCon… (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260803T200400Z-agent-md-documentation-conventions-when-3370)
- De-duplicate archive-structure watermark parsing in `board_data.py` by calling the shared `_parse_archive_structure` helper instead of inlining identical JSON parsing logic. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260716T200417Z-deduplicate-archive-structure-watermark-cbc2)
- Enable UV_MALWARE_CHECK=1 in CI to block known-malicious packages (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260716T200418Z-enable-uv-malware-check-1-in-ci-to-block-31fb)
- Split tests/server/test_action_mixin.py (1489 lines) into per-method test modules (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260717T201911Z-split-tests-server-test-action-mixin-py-60b7)
- Make Trivy container scan blocking on CRITICAL severity findings in release.yml (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260717T201912Z-make-trivy-container-scan-blocking-on-cr-94a9)
- Decomposed `build_parser()` into per-module `register_subparser()` functions so each subcommand's argument definitions live alongside their handlers in the corresponding `commands_*.py` module. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T202047Z-decompose-build-parser-by-delegating-sub-61d1)
- Extract ``_imap_cross_folder_fallback`` and ``_ensure_folder_hierarchy`` shared helpers in ``server/adapters.py``, refactoring the cross-folder IMAP resolution fallback duplicated across ``_handle_delete``, ``_archive_and_delete``, ``_run_batch_delete_background``, and ``_run_batch_archive_background``. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T202047Z-refactor-archive-and-delete-in-server-ac-45c4)
- Refactor _gather_account_board_data in server/views/board.py (153 lines, depth-6 nesting) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T202047Z-refactor-gather-account-board-data-in-se-3cf2)
- Remove Dependabot for ecosystems already covered by Renovate to eliminate duplicate dependency PRs (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T202325Z-remove-dependabot-for-ecosystems-already-f7af)
- Split tests/cli/test_cli_detect.py (1523 lines) into per-theme test modules (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260718T203603Z-split-tests-cli-test-cli-detect-py-1523-d05d)
- Extract shared LLM parameter docstrings in db/archive.py to avoid drift (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260718T203605Z-extract-shared-llm-parameter-docstrings-599a)
- Unify JSON watermark loading helpers across config_sync_agent.py and classifier.py (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260718T203605Z-unify-json-watermark-loading-helpers-acr-9c2f)
- Docker polish: remove dead envsubst code from ``entrypoint.sh``, add SIGTERM handler to ``ingest --watch``, align dev ``docker-compose.yml`` with deploy (drop stray board label, add ``--heartbeat-file``, document config filename difference). (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260704T204106Z-docker-polish-remove-dead-envsubst-code-64e9)
- Split `tests/db/test_db.py` (1088 lines) into seven per-theme test modules under `tests/db/`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260719T204611Z-split-tests-db-test-db-py-1088-lines-int-08e9)
- Split tests/cli/test_cli_refine.py (987 lines) into domain-focused test modules (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260719T204612Z-split-tests-cli-test-cli-refine-py-987-l-f637)
- Split `tests/cli/test_cli_refine.py` (987 lines) into five domain-focused modules under `tests/cli/`: `test_cli_refine_password`, `test_cli_refine_llm`, `test_cli_refine_manual`, `test_cli_refine_pipeline`, and `test_cli_refine_prompt_hosts`. Shared helpers extracted to `tests/cli/conftest.py`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260719T204612Z-split-tests-cli-test-cli-refine-py-f637)
- Extract shared smtplib.SMTP connection helper in SmtpClient._connect_starttls / _connect_plain (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260719T204614Z-extract-shared-smtplib-smtp-connection-h-5102)
- Updated 11 stale references from the deleted `docs/config/mail.local.example.yaml` to `config/config.example.json` across README and docs. Removed empty `docs/config/` directory and its `.gitkeep`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260728T204809Z-update-stale-documentation-references-fr-84a5)
- AGENT.md: Documentation conventions — When a PR removes or renames a file that is hyperlinked or referenced in docume… (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260728T204810Z-agent-md-documentation-conventions-when-e08a)
- Added cross-reference update rule to AGENT.md documentation conventions: when a PR removes or renames a file referenced in docs, all cross-references must be updated in the same PR. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260728T204810Z-update-agent-md-documentation-conventions)
- Add pytest-timeout to CI to prevent hung tests from consuming full job timeout (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260720T205011Z-add-pytest-timeout-to-ci-to-prevent-hung-2b46)
- Added `pytest-timeout` as a dev dependency and configured per-test (`timeout = 30`) and session-level (`session_timeout = 900`) timeouts in `[tool.pytest.ini_options]`. Limits are set with large safety margins above the measured worst-case single test (~4 s) and full-suite wall time (~75 s). On a timeout hit the logged timeout value and offending test id make it straightforward to distinguish a true deadlock from a slow-but-healthy test. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260720T205011Z-add-pytest-timeout-to-dev-deps)
- Split oversized server test_board_views.py into focused unit-test modules. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260720T205012Z-split-tests-server-test-board-views-unit-c030)
- Split tests/cli/test_commands_detect.py (926 lines) into per-handler test modules (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260721T205654Z-split-tests-cli-test-commands-detect-py-d7f6)
- Remove dead dependabot-auto-merge.yml workflow (Renovate-only repo) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260722T205817Z-remove-dead-dependabot-auto-merge-yml-wo-12c3)
- Split tests/pipeline/test_ingest.py (887 lines) into per-theme test modules (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260722T205817Z-split-tests-pipeline-test-ingest-py-887-5b4a)
- config-standard cutover: remove auto-mail env-overlay + plain-JSON fallback (config is sole source) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260721T210331Z-config-standard-cutover-remove-auto-mail-79ff)
- Split tests/smtp/test_smtp_client.py (872 lines) into per-operation test modules (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260723T210507Z-split-tests-smtp-test-smtp-client-py-872-3c04)
- Split tests/server/test_view_mixin.py (741 lines) into per-view-method test modules (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260723T210514Z-split-tests-server-test-view-mixin-py-74-76f5)
- Split `tests/config/detect/test_detect.py` (679 lines) into four domain-focused
  modules: `test_detect_models.py`, `test_detect_provider.py`,
  `test_detect_autoconfig.py`, and `test_detect_consistency.py`, with shared
  helpers moved to `tests/config/detect/conftest.py`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260724T211230Z-split-tests-config-detect-test-detect-py-d8d2)
- Split tests/imap/test_imap_messages.py (538 lines) into four per-operation modules: test_imap_search_uids.py, test_imap_fetch_message.py, test_imap_delete_message.py, and test_imap_move_message.py. Moved the shared ``_uid_side_effect`` helper to tests/imap/conftest.py. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260724T211231Z-split-tests-imap-test-imap-messages-py-5-305f)
- Extract duplicated SBOM generation job from ci.yml and release.yml into a reusable workflow (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260724T211233Z-extract-duplicated-sbom-generation-job-f-cb26)
- Extracted the duplicated SBOM generation job from ci.yml and release.yml into a reusable workflow at `.github/workflows/sbom.yml`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260724T211233Z-extract-duplicated-sbom-job-to-reusable-w)
- Register or delete the unregistered changelog/20260731T203154Z-register-changelog-20260731t023847z-regi-a746.misc.md fragment in docs/modules.yaml (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260801T211609Z-register-or-delete-the-unregistered-chan-dd82)
- Fix three CodeQL alerts: remove unused `_logger` in `commands_serve.py`, register `_STATIC_ROBOTSIX_UI_JS`/`_STATIC_ROBOTSIX_UI_CSS` in the suppression tuple, and sanitize newlines from `account_id` before logging. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T212747Z-ci-fix-out-of-scope-ci-failure-codeql-py-3185)
- Migrate account config from mail.local.yaml to deploy-managed config.json (SecretStr passwords, committed schema) (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260718T213146Z-migrate-account-config-from-mail-local-y-27b1)
- Refactor detect/verify CLI into pure diagnostic tool: report-only output, no config writing, OAuth tokens to runtime dir (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260718T213158Z-refactor-detect-verify-cli-into-pure-dia-240d)
- Enable mypy_baseline periodic workflow to track mypy baseline drift (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260731T213327Z-robotsix-auto-mail-enable-mypy-baseline-f349)
- AGENT.md: Testing conventions — clarify that changelog fragments are claimed in `docs/modules.yaml` (core module) via the single `changelog/*.md` glob, not per-fragment path entries. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260802T213943Z-agent-md-testing-conventions-changelog-f-47b2)
- Add container image provenance and SBOM attestation to release workflow (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260725T214436Z-add-container-image-provenance-and-sbom-45f9)
- Split tests/server/test_views_detail.py (644 lines) into per-render-function test modules (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260725T214436Z-split-tests-server-test-views-detail-py-7361)
- Extract shared helper `_reload_accounts_and_interval` in `commands_serve.py` to eliminate ~26 lines of duplicated account-reload and interval-computation logic between `_reconcile_loop` and `_ingest_loop`. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260807T215025Z-extract-shared-account-reload-helper-fro-0622)
- Add native-path Quick Start section to docs/index.md (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260718T215330Z-add-native-path-quick-start-section-to-d-b24d)
- Consolidate modules observability, core: logging/tracing setup is foundational infrastructure (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260702T221229Z-consolidate-modules-observability-core-l-7593)
- test gap: add unit tests for src/robotsix_auto_mail/server/_account_mixin.py (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260724T223517Z-test-gap-add-unit-tests-for-src-robotsix-4769)
- Split the Dockerfile builder stage into two layers: a dependency-install layer (cached until `pyproject.toml` or `uv.lock` changes) and a project-source layer that copies `src/` and installs with `--no-deps`. This keeps third-party dependency installs cached across source-only rebuilds, cutting image build time from minutes to seconds in the common case. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260807T224504Z-split-dockerfile-builder-stage-into-two-73e4)
- agent_limitation — The agent made six sequential edit_file → run_command (pytest) → think cycles (observation (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T225054Z-agent-limitation-the-agent-made-six-sequ-77d8)
- optimization — 240 observations (12× median) dominated by 110 run_command tool calls, of which 78 are tri (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T225054Z-optimization-240-observations-12-median-6e9d)
- Updated PR template and contributing guide to reference towncrier
  changelog fragments instead of direct `CHANGELOG.md` edits. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260705T230424Z-update-pr-template-and-contributing-guid-eea1)
- Missing re-export: `db/archive.py` public symbols not exposed through `db/__init__.py` (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260726T230647Z-missing-re-export-db-archive-py-public-s-ab47)
- Replace detect-secrets (Yelp, unmaintained since May 2024) with
    gitleaks (v8.30.1, actively maintained) for secret scanning:
    gitleaks-docker pre-commit hook, .gitleaks.toml config, and a new
    secret-scanning CI workflow with SARIF upload to GitHub Code Scanning. (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260719T231858Z-replace-detect-secrets-with-gitleaks-829d)
- Add CODE_OF_CONDUCT.md (Contributor Covenant v2.1) and expand CONTRIBUTING.md with community health sections (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260703T232718Z-add-code-of-conduct-md-contributor-coven-cbe8)
- Boilerplate: Deterministic Periodic-Agent Proposal — Auto-Approve (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260724T233442Z-boilerplate-deterministic-periodic-agent-039b)
- Remove duplicate unregistered changelog fragment (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260719T233517Z-remove-duplicate-unregistered-changelog-e842)
- Remove duplicate changelog fragment from PR #1006 merge (https://github.com/damien-robotsix/robotsix-auto-mail/issues/20260728T235945Z-remove-duplicate-changelog-fragment-from-1a4c)


<!-- This file is generated by towncrier. Do not edit by hand. -->
<!-- History below the first machine-generated release was written manually. -->

### Other changes

- Fix unsubscribe link failing with Bad Request when ``List-Unsubscribe`` header contains multiple comma-separated URIs (e.g. ``<https://...>, <mailto:...>``). The header is now parsed per RFC 2369 to extract a single preferred URI (https/http over mailto).
- Removed ``default_account_id`` setting and all single-account-fallback code paths.
  The account a request/CLI command operates on is now mandatory and explicit.
  The initial board view defaults to the first account in configured order.
  CLI ``--account`` is now required on ``board``, ``probe``, ``triage``, and
  ``config-sync`` subcommands.
- POST action endpoints (`/move`, `/archive`, `/delete`, `/save-notes`, batch
  operations, etc.) now accept JSON request bodies in addition to form-encoded
  data, so clients sending ``Content-Type: application/json`` receive the same
  behaviour as the board UI. Malformed JSON with a JSON content type returns a
  clear 400 error.
- Sanitize HTTP 400 error responses in ``_action_mixin.py`` and
  ``_triage_mixin.py``: replace raw ``str(exc)`` disclosure with
  generic ``"Invalid request"`` message and server-side
  ``logger.exception`` traceback logging.
- Added testing convention to AGENT.md: novel functions in fix/recovery paths must have positive-path unit tests exercising the real function body, mocking at dependencies, not the function itself.
- Wrap ``sys.path.insert(0, …)`` in ``_load_field_mappings`` inside a
  ``try/finally`` block that restores the original ``sys.path`` so the
  mutation is scoped to the import and does not permanently pollute the
  global import path.
- Wire the calendar write path: moving a card to ``TO_CALENDAR`` now calls the
  ``update_calendar_correlation_id`` and ``update_calendar_event_ref`` DB write
  functions (previously defined but never invoked), so the calendar columns are
  populated instead of remaining permanently empty.
- Added AGENT.md rule: when adding, removing, or renaming a config field on
  ``MailAccountsConfig`` or ``MailConfig``, update ``docs/configuration.md`` and
  keep ``scripts/config/check_config_sync.py``'s ``_CONFIGURATION_MD_CONTAINER_KEYS``
  in lockstep.
- Thread the configured `detector_level` through the detect CLI flow so
  `detect_provider` receives the operator-configured LLM tier instead of
  always defaulting to level 1.  The `_detect_settings`, `_verify_and_refine`,
  and `_refine_with_llm` helpers now accept and forward a `level` / `llm_level`
  keyword argument, and `_cmd_detect` captures both return values of
  `resolve_llm_tier(APP_DETECTOR)`.
- Removed dead `_patch_serve_board_deps` autouse fixture and renamed `tmp_db_path` to `_fake_db_path` in `tests/server/_view_mixin_helpers.py` to eliminate collision with root conftest. Deleted empty `tests/db/conftest.py`.
- Removed orphaned `_VALID_CONFIDENCE_LEVELS` re-export from `triage._constants` (was imported but never used; peers route through `validate_confidence` instead).
- Add `level` parameter to `propose_archive_subfolder_llm` (default 1) so
  the configured classifier LLM tier level is threaded from all three call
  sites (`_action_mixin.py` move/archive handlers,
  `get_archive_subfolder`, `_fill_missing_archive_hints`) instead of being
  discarded in favour of the internal hardcoded `level=1`.
- Added `GET /chat-skill` endpoint returning `text/markdown` with YAML frontmatter and API documentation + safety rules, per the robotsix-standards chat-access standard §1.  Added `robotsix.deploy.chat-access: "true"` compose label on the board service.
- Remove accidentally committed empty `changelog/test-file` artifact.
- Added dedicated unit tests for ``ConfigVersionStore`` covering atomic writes, corrupt-JSON recovery, version numbering, retention trimming, snapshot retrieval, and metadata extraction.
- Resolve pre-existing CI failures: upgrade cryptography transitive dependency from 48.0.1 to 50.0.0, fix CodeQL py/log-injection in _settings_mixin.py, fix CodeQL py/unused-global-variable in _constants.py, and remove unused local variable in commands_serve.py.
- Grouped board header controls (Recheck connections, Add Account, Settings)
  into an aligned flex toolbar.  Embedded the add-account auto-detection flow
  directly into the Settings page via an iframe so it is a first-class part of
  settings rather than only reachable from the standalone ``/add-account`` page.
- Updated `docs/configuration.md` to document the tiered model configuration (`models.level1`–`level4`) and per-application level fields (`triage_level`, `classifier_level`, `rules_level`, `detector_level`, `draft_level`), replacing the removed `llm_provider_model` field. Updated `scripts/config/check_config_sync.py` to match.
- **Breaking:** replace flat ``llm_provider_model`` field with tier-based ``models`` config (``level1``–``level4`` overrides) and per-application level fields (``triage_level``, ``classifier_level``, ``rules_level``, ``detector_level``, ``draft_level``). Blank overrides now resolve to the llmio tier default for that level, and each task selects its tier via its configured application level. Level 4 is wired through even though llmio does not yet define ``LEVEL4_DEFAULT``.
- Enable credit_balance periodic workflow to monitor OpenRouter credit balance.
- Derive package version from git tags via hatch-vcs so `--version` and `__version__` track the release tag instead of reporting a hardcoded `0.0.0`.
  Remove the dead `--generate-notes` flag from the `gh release create` step (silently ignored when `--notes-file` is present).
- Refactored `_run_batch_delete_background` and `_run_batch_archive_background` into thin wrappers around a new shared `_run_batch_background` parameterised driver, removing ~150 lines of duplicated IMAP orchestration code.
- Clear repo-wide ruff lint debt (remove unused `# noqa: S310` directives).
- Removed orphaned CLI config file-write helpers
  (`_load_accounts_from_file`, `_existing_account_ids`,
  `_existing_accounts_for_append`, `_find_existing_account`) that were
  superseded by inline logic in the `detect` command.
- Removed an unregistered duplicate changelog fragment (`20260731T123451Z-test-gap-add-unit-tests-for-src-robotsix-333e.misc.md`) that was a near-duplicate of the already-registered reconcile-mixin fragment.
- Fix all remaining ruff lint and format violations across the whole repository, and register all unregistered changelog fragments and test files in ``docs/modules.yaml`` so the module-registration completeness check passes on CI.
- The ``serve`` command now starts a background ingest loop that
  automatically fetches new mail for all configured accounts on the
  ``ingest_interval_minutes`` interval. Previously the web server only
  displayed mail already in the database; a separate ``ingest --watch``
  process was required. The board also shows a warning banner when
  accounts are configured but zero mails have been fetched.
- Centralized watermark sentinel values ``"running"`` and ``"idle"`` into shared constants ``_WATERMARK_RUNNING`` and ``_WATERMARK_IDLE`` in ``core/_constants.py``, replacing bare string literals across 6 source files.
- Removed a stale `per-file-ignores` entry for the non-existent `src/robotsix_auto_mail/detect.py` and updated the associated deptry comment to reference the actual lazy-import sites.
- Enable mypy_baseline periodic workflow to track mypy baseline drift
- Added a settings panel at `/settings-panel` listing all configured mail accounts with per-account delete buttons. Deleting an account removes it from the persisted `config/config.json` and updates the running server immediately.
- Web UI add-account now seeds the per-account settings store and initializes the
  new account's database immediately, ensuring the account config is persisted in
  the managed configuration plane. The reconcile loop reloads accounts from the
  config file on every cycle so newly added accounts begin fetching mail without
  a restart. On boot, accounts discovered from existing settings stores are merged
  with config-file accounts so web-UI-added accounts survive even when the deploy
  system overwrites ``config/config.json``.
- Removed unused `set_component_setting` function from `db/queries.py` and its re-export in `db/__init__.py` (only the plural `set_component_settings` is used).
- Remove the spurious `changelog.d/*.md` glob from the `core` module's `paths` list in `docs/modules.yaml` (the `changelog.d/` directory does not exist; only `changelog/` does).
- Added dedicated unit tests for `_ReconcileMixin._handle_reconcile` covering idempotency guard, per-account thread spawning in aggregate mode, and single-thread non-aggregate path.
- Fix pre-existing ruff format violations in `commands_ingest.py` and `test_cli.py` (style-only). Fix pre-existing mypy errors in `commands_serve.py` (wrong attribute name `acct.id` → `acct.account_id`) and `cli/__init__.py` (type-narrowing on reused variable names in the no-command auto-start path). Pin uv to 0.12.1 in the setup action and use `UV_PREVIEW=1` for the `uv audit` CI step for broader uv-version compatibility.
- Fix crash when adding the first account to a fresh-deploy config with ``accounts=[]`` and ``default_account_id=''`` — the handler now falls back to the first account's id when the existing default is empty, and wraps ``MailAccountsConfig`` construction in try/except so validation errors render the form with an error message instead of crashing the request handler.
- Fix pre-existing ruff violations (formatting, naming, import ordering) across 26 source and test files, upgrade pymdown-extensions transitive dependency to resolve uv audit advisory, and fix CodeQL py/mixed-returns alert in cli/commands.py.
- Honour ``X-Forwarded-Host`` and ``Forwarded: host=`` headers in the CSRF
  origin check so browser POSTs behind the fleet reverse proxy are no longer
  rejected with 403 Forbidden.
- Updated `docs/deployment.md` to reflect the config-driven ingester contract (`ingest_mode` / `heartbeat_file`) instead of the old `--watch` / `--heartbeat-file` CLI flags.
- Add unit tests for SettingsStore CRUD, secret masking, field validation,
  and request-handler tests for GET/POST /settings endpoints.
- Strengthen AGENT.md changelog-fragment registration rule with explicit "applies to ALL PRs" note and mandatory ``git grep`` self-check to eliminate per-fragment cleanup-ticket churn.
- Register changelog fragment `20260730T181056Z-split-tests-pipeline-test-parser-py-508-0ab6.misc.md` in `docs/modules.yaml`.
- docs: fix remaining stale "YAML config file" references in connecting.md, deployment.md; register changelog fragment in docs/modules.yaml
- Update all `config/mail.local.yaml` references in docs (connecting, deployment, troubleshooting) and AGENT.md to `config/config.json`, and replace YAML config examples with JSON `accounts:` list shape.
- Fix CSRF guard rejecting same-origin POSTs behind a reverse proxy by comparing the request's ``Origin`` header against its ``Host`` header (proxy-aware same-origin check).
- Add `ProviderEntry` re-export to `config.detect.__init__` so it is importable from the public package alongside its peers `DetectedProvider`, `DetectionError`, and `MailProvider`.
- Wire one-time settings import from central-deploy on first boot so each account's per-component settings store is seeded automatically when ``CENTRAL_DEPLOY_EXPORT_URL`` is set. The import is idempotent — restarting the service never overwrites locally-edited settings. Document the GET/PUT ``/settings`` API in ``docs/configuration.md``.
- Split `tests/cli/test_commands_serve.py` into two modules: the
  reconcile-loop tests move to `test_commands_serve_reconcile.py`
  and the local `_accounts()` helper is replaced by the shared
  conftest import.
- The ingester service in `deploy/docker-compose.yml` now has an explicit `command: ingest` so it no longer relies on the no-subcommand auto-start fallback. The CLI no-command fallback now emits a clear diagnostic error message (instead of just argparse help text) when it cannot auto-start the ingest watch loop, making container restart-loop failures self-documenting in the logs.
- Fixed the **Detect Settings** button on the Add Mail Account form so it works without pre-filled IMAP/SMTP host fields. The button now uses ``formnovalidate`` to bypass browser-side form validation, allowing the server-side detection to auto-populate settings from the email domain alone.
- Split `tests/server/test_server_archive_delete.py` (790 lines) into four domain-focused modules: `test_server_archive_delete_basic.py`, `test_server_archive_delete_stale_uid.py`, `test_server_archive_delete_cross_folder.py`, and `test_server_archive_proposal_endpoint.py`.
- Updated 11 stale references from the deleted `docs/config/mail.local.example.yaml` to `config/config.example.json` across README and docs (README.md, docs/index.md, docs/configuration.md, docs/connecting.md, docs/deployment.md). Removed empty `docs/config/` directory and its `.gitkeep`.
- Added cross-reference update rule to AGENT.md documentation conventions: when a PR removes or renames a file referenced in docs, all cross-references must be updated in the same PR.
- Split ``tests/server/test_server_draft.py`` (592 lines) into two focused modules: ``test_server_draft_manage.py`` (draft move/save/generate) and ``test_server_draft_send.py`` (send-draft reply/forward/validation/buttons). Extracted the shared ``_patch_smtp_and_imap`` context manager into ``tests/server/_draft_helpers.py``.
- Add missing re-export of ``DEFAULT_RULES_HEADER`` in ``robotsix_auto_mail.triage``
- Split `tests/server/test_account_mixin.py` (562 lines, 37 tests, 4 classes)
  into four per-class modules and extract shared helpers
  (`_AccountMixinFakeHandler`, `_make_post_body`) to `tests/server/_test_helpers.py`.
- Added `pytest-randomly` as a dev dependency and configured `--randomly-seed=last` in CI's pytest invocation to randomize test execution order and surface hidden ordering dependencies.
- Split 731-line ``tests/server/test_draft_mixin_send_generate.py`` into four focused test modules: ``test_draft_mixin_send.py``, ``test_draft_mixin_generate.py``, ``test_draft_mixin_redirect.py``, and ``test_draft_mixin_draft_generator.py``. Extracted shared ``_patch_llm`` and ``_insert_inbox`` helpers to ``tests/server/_draft_helpers.py``.
- Updated board URL comment in docker-compose.yml to use fixed port 8080 instead of ${BOARD_PORT:-8080}. (mill: Replace `${BOARD_PORT}` with fixed `"8080:8080"` in root `docker-compose.yml` (20260727T021927Z-replace-board-port-with-fixed-8080-8080-f754))
- Migrated `command:` override and `BOARD_PORT` env var out of docker-compose per the config-ownership standard. Added `ingest_mode` (Literal["watch","once"], default "once") and `heartbeat_file` (str, default "") to `MailConfig` and `config.example.json`. Entrypoint `main()` now merges config values with CLI args for ingest, and auto-starts the watch loop when no command is given and `ingest_mode` is "watch". Removed `command: ingest --watch --heartbeat-file ...` from both `docker-compose.yml` and `deploy/docker-compose.yml`. Replaced `${BOARD_PORT:-8080}:8080` with fixed `"8080:8080"`. All config-sync artifacts (`_field_map.py`, `docs/configuration.md`, `config.schema.json`, `test_config_sync.py`) updated. Full test suite: 507 config + CLI tests pass.
- Re-export `db.archive` public symbols (`ARCHIVE_ROOT`, `ArchiveError`, `ArchiveStructure`, `determine_archive_structure`, `setup_archive`) through `db/__init__.py` so callers can use `from robotsix_auto_mail.db import setup_archive` instead of the deep import path.
- Ingest `--watch` mode now stays alive when no accounts are configured, entering an idle heartbeat loop instead of exiting. The loop re-checks the config each cycle so newly added accounts are picked up without a restart (liveness heartbeat is written every cycle). Non-watch commands (`probe`, `ingest`, `board`, `serve`) still exit with an error on empty config.
- Split ``tests/server/test_views_detail.py`` (644 lines) into 8 per-render-function test modules plus a shared ``_view_helpers.py`` helper.
- Also register this PR's own changelog fragment `changelog/20260725T041822Z-register-pr-973-s-changelog-fragment-in-b0fb.misc.md` in `docs/modules.yaml`, and clarify CHANGELOG.md entry to use the full fragment filename.
- Register missing changelog fragment `changelog/20260725T030052Z-fix-startup-failure-on-ci-and-release-im-96b3.misc.md` in `docs/modules.yaml`.
- Register this PR's own changelog fragment `changelog/20260725T041822Z-register-pr-973-s-changelog-fragment-in-b0fb.misc.md` in `docs/modules.yaml`.
- Fixed ``startup_failure`` in both the ``CI`` and ``Release image`` workflows
  on main: the ``sbom.yml`` reusable workflow had an internal permissions
  mismatch (workflow-level ``permissions: {}`` vs the job's ``contents: read``)
  that caused GitHub to reject any caller, and the release workflow's ``sbom``
  job inherited ``{}`` from its workflow-level permissions, which was
  insufficient for the called reusable workflow. Also added missing
  ``security-events: write`` to the ``security`` job in ``ci.yml`` so the
  ``python-security.yml`` reusable workflow can upload SARIF results.
- Add `parallel = true` to `[tool.coverage.run]` in `pyproject.toml` so that coverage data from parallel xdist workers is properly merged.
- Remove dead periodic workflow config ``.robotsix-mill/periodic/security_posture.yaml`` (name-only file, not in available catalog).
- Make `password` optional in per-account config; accounts without a password are skipped at runtime with a clear warning rather than crashing. The service can now deploy with zero fully-credentialed accounts and activate them later via config update.
- Added triage boilerplate for deterministic periodic-agent proposals
  (audit, bc_check, module_size, copy_paste, test_gap, docstring_coverage)
  to `.robotsix-mill/periodic/triage_boilerplate.yaml`.
- Replace inlined `_RetryConfig`/`_call_with_retry` in `detector.py` with imports
  from `robotsix-http` (shared library).  Replace `urllib3.PoolManager` with
  `httpx.Client` and `robotsix_http.call_with_retry` for autoconfig and MX-lookup
  HTTP calls.
- Fix 17 instances of Python 2 `except X, Y:` syntax that silently failed to catch all listed exception types in Python 3.
- Replace `pip install` instructions with `uv`-native equivalents in developer setup docs (README.md, CONTRIBUTING.md, testing.md).
- Increase the smoke-test readiness-poll timeout from 20 s to 60 s
  (120 iterations × 0.5 s) so the first import on a cold bytecode cache
  completes before the poll gives up.  The import chain for
  ``robotsix_auto_mail.cli`` pulls in pydantic, robotsix_config,
  smtplib (→ email → …), and the cumulative compile time can exceed
  10 s in a fresh venv.
- Added an "Add Account" button to the mail board web UI.  Operators can
  now create new mail accounts entirely through the browser — no manual
  config-file editing or container restart required.  The new account
  appears in the board picker immediately after creation.
- Split `tests/pipeline/test_ingest.py` (887 lines) into four domain-focused
  modules: `test_ingest_dataclass.py`, `test_ingest_core.py`,
  `test_ingest_dryrun.py`, and `test_ingest_archive_triage.py`.
- Removed `.github/workflows/dependabot-auto-merge.yml` (dead workflow — Renovate handles all dependencies, Dependabot is intentionally absent)
- Add inlined exponential-backoff retry primitives (`_RetryConfig`, `_call_with_retry`, `_is_transient_urllib3`) to autoconfig and MX-lookup HTTP calls for resilience against transient network errors (timeouts, connection drops).
- Thread `api_key` and `provider_model` through `_check_unsubscribe_for_to_delete` to `_detect_unsubscribe_for_sender`, so that unsubscribe detection uses the explicitly-provided credentials instead of silently falling back to config-level resolution when a caller passes `--api-key` or a `provider_model` override to `run_triage_agent`.
- Split `tests/smtp/test_smtp_client.py` (872 lines) into five domain-focused modules: test_smtp_exceptions, test_smtp_connect, test_smtp_send, test_smtp_close, and test_smtp_isolation.
- Split `tests/server/test_view_mixin.py` (741 lines) into four domain-focused modules (`_static`, `_board`, `_email`, `_archive`) with a shared `_view_mixin_helpers.py` fixture module.
- Replace duplicated IMAP archive-move logic in `_archive_and_delete`'s cross-folder fallback with a direct call to `_imap_archive_move`, eliminating ~25 lines of duplicated code (ImapClient lifecycle, delimiter discovery, folder-hierarchy creation) and ensuring future archive-move changes apply uniformly to both code paths.
- Removed dead pydantic v1 compatibility `hasattr(field_info, "is_required")` guard in `_render_mailconfig_surface()`. The project requires pydantic ≥ 2.0 where `FieldInfo.is_required()` is always available.
- Split ``tests/cli/test_commands_detect.py`` (926 lines) into three domain-focused modules: ``test_commands_detect_report.py``, ``test_commands_detect_probe.py``, ``test_commands_detect_cmd.py``, following the AGENT.md ~500-line threshold.
- Removed all environment-variable overrides for LLM secrets and settings (`LLM_API_KEY`, `LLM_PROVIDER_MODEL`). The API key now resolves solely from `config.json`'s `llm_api_key` field, and the provider-model from `llm_provider_model`. The legacy plain-JSON fallback loader in `_load_accounts_from_file` has been removed — `robotsix_config.load_config` is the only config-loading path. A schema-drift check (`config/config.schema.json` vs the pydantic model) has been added to the CI config-sync step.
- Migrate config to typed-JSON contract: removed fallback config loading in loader.py (now uses robotsix_config exclusively), replaced YAML example with multi-account JSON example (config/config.example.json), and updated CI config-sync checker and advisory agent to validate against JSON instead of YAML.
- Introduced ``RobotsixMailError`` base class in ``src/robotsix_auto_mail/errors.py``,
  re-exported from the package root. All 9 domain exceptions now inherit from
  ``RobotsixMailError`` instead of plain ``Exception``, and the CLI entrypoint
  ``main()`` catches ``RobotsixMailError`` for a clean non-zero exit + log.
- docs/modules.yaml: remove duplicate `d241` path entry from core module's path list without introducing `# Before` / `# After` annotations into the YAML file.
- Added 25 direct unit tests for `commands_detect.py` handler functions (`_build_detect_report`, `_cmd_detect`, `_probe_capabilities`, `_print_detect_report`).
- Split ``tests/server/test_board_views_unit.py`` (893 lines, 47 tests) into five
  per-function test modules: ``test_board_views_columns.py``,
  ``test_board_views_batch_banner.py``, ``test_board_views_gather.py``,
  ``test_board_views_shell.py``, and ``test_board_views_build.py``.
- Add triage boilerplate pattern for completeness_check missing re-export tickets.
- Added scope-triage boilerplate for CHANGELOG.md, docs/modules.yaml, and changelog/*.md accompanying-documentation patterns.
- Added `.robotsix-mill/periodic/triage_boilerplate.yaml` periodic workflow config to enable the triage boilerplate scanner for this repo.
- Tell the triage-rules flash LLM which actions the automated triage agent
  can actually assign (HUMAN_TRIAGE, TO_ARCHIVE, TO_DELETE, TO_ANSWER) so it
  does not suggest rules using human-only actions like TO_CALENDAR.
  Also fix the DEFAULT_RULES_HEADER example that incorrectly referenced TO_CALENDAR.
- Add `agent_check` periodic mill workflow (`.robotsix-mill/periodic/agent_check.yaml`) to validate agent output model contracts.
- Enable the `trace_review` periodic to flag anomalous Langfuse traces from LLM-driven agent runs (triage, unsubscribe detection, config-sync).
- Re-export `save_accounts` from `robotsix_auto_mail.config` so callers can use the canonical import path instead of reaching into the `loader` submodule.
- Replace detect-secrets (Yelp, unmaintained since May 2024) with
  gitleaks (v8.30.1, actively maintained) for secret scanning:
  gitleaks-docker pre-commit hook, .gitleaks.toml config, and a new
  secret-scanning CI workflow with SARIF upload to GitHub Code Scanning.
- Extract shared ``_create_smtp_connection`` helper in ``SmtpClient`` to
  deduplicate plain-SMTP connection logic between ``_connect_starttls``
  and ``_connect_plain``.
- Added a native (non-Docker) Quick Start section to `docs/index.md` with a complete five-step "from zero to working" narrative, and cross-referenced it from `docs/connecting.md`'s Docker Quick Start.
- Narrow exception types in `_probe_capabilities` from bare `Exception` to
  `(OSError, _IMAP4_ERROR, ImapError)` and `(OSError, _SMTP_EXCEPTION, SmtpError)`,
  and fix the `_block_network` test fixture to raise `ConnectionRefusedError`
  (subclass of `OSError`) so narrow exception handling works correctly in tests.
- `detect` is now report-only: prints a JSON diagnostic report to stdout
  (schema-shaped keys: imap_host/port/tls_mode, smtp_*, username, capabilities,
  login_ok) and writes no config file.  Removed ``--output``, ``--overwrite``,
  and ``--stdout`` flags.  Passwords are never printed.  Operators paste the
  detected values into the deploy Configure panel.
- Removed ``save_accounts`` from ``config.loader`` (no callers remain).
- Removed ``_existing_account_ids``, ``_existing_accounts_for_append``,
  ``_find_existing_account``, ``_load_accounts_from_file``,
  ``_normalise_legacy_account``, and ``_report_failure`` from
  ``cli.config`` (all served the now-removed file-write path).
- Type `password`, `llm_api_key`, `oauth2_token`, `oauth2_client_secret`,
  and `langfuse_secret_key` as `pydantic.SecretStr` so the JSON Schema
  emits `writeOnly` for the Configure panel.  Secrets are now masked in
  `model_dump_json()`; use `_dump_config_json()` for file persistence.
- Drop `mail.local.yaml` as a runtime config source: the YAML fallback
  in `_load_accounts_from_file` is removed; the `detect` default output
  path is now `config/config.json`.
- Commit `config/config.schema.json` for the central-deploy Configure
  panel form rendering.
- Extract shared `load_json_watermark` / `save_json_watermark` helpers into `db.queries`, deduplicating watermark loading logic between `config_sync_agent` and `triage.classifier`.
- Split `tests/cli/test_cli_detect.py` (1523 lines) into four focused test
  modules — `test_cli_detect_basic.py`, `test_cli_detect_microsoft.py`,
  `test_cli_detect_accounts.py`, and `test_cli_detect_settings.py` — with
  shared helpers extracted into `tests/cli/conftest.py`.
- Extracted shared LLM parameter documentation in ``db/archive.py`` into a module-level ``_LLM_PARAM_DOCS`` constant, referenced from both ``determine_archive_structure`` and ``setup_archive`` docstrings to prevent drift.
- Add docstring to ``register_subparser`` in ``commands_ingest.py``
- Add docstring to ``register_subparser`` in ``commands_triage.py``.
- Add docstring to `register_subparser` in `commands_detect.py`.
- Added docstring to ``register_subparser`` in ``commands_config_sync.py``.
- Add docstring to ``main()`` entry point in ``src/robotsix_auto_mail/dev/autoupdate.py``.
- Enable `docstring_coverage` periodic agent to scan for undocumented public API and propose draft tickets.
- Enable `health` periodic agent to inspect the repository across eight dimensions (testing, typing, linting, security, documentation, dependency freshness, code complexity, CI coverage) and file draft tickets for gaps.
- Enable the `survey` periodic agent to discover similar projects, study their approaches, and propose concrete improvements for the repo.
- Added `repo_description_sync` periodic agent config (`.robotsix-mill/periodic/repo_description_sync.yaml`).
- Remove `tests/server/test_action_mixin.py` (1489 lines) now that all test classes have been extracted into individual per-method modules under `tests/server/`.
- Add dedicated unit tests for ``MailConfig``, ``MailAccount``, and ``MailAccountsConfig``
  model validators (``tests/config/test_model.py``), covering template-literal
  detection, TLS mode / log level / log format validation, account-id format
  enforcement, multi-account uniqueness checks, and secret-field masking in
  ``__repr__`` / ``__str__``.
- Re-export `normalize_archive_subfolder` from `robotsix_auto_mail.triage` so it can be imported from the public package without reaching into the `classifier` submodule.
- Re-export ``ParseError`` and ``parse_message`` from ``robotsix_auto_mail.pipeline`` so consumers can import them from the public API instead of the private ``_parse`` module.
- De-duplicate archive-structure watermark parsing: `board_data.py` now calls the shared `_parse_archive_structure` helper instead of inlining ~18 lines of identical JSON parsing logic.
- Enable `UV_MALWARE_CHECK=1` in CI `uv sync` steps and Dockerfile builder stage to consult the OpenSSF Malicious Packages database before installing packages.
- Enable audit periodic agent in robotsix-mill.
- Re-export ``get_account_health`` and ``write_account_health`` from ``robotsix_auto_mail.db`` so they are available via the package's public API surface, consistent with all other public functions in ``db.queries``.
- Enable `completeness_check` periodic agent (`.robotsix-mill/periodic/completeness_check.yaml`).
- Enable `bc_check` periodic agent (`.robotsix-mill/periodic/bc_check.yaml`).
- Reorganized the `core` module: moved `_constants.py`, `_llm_agent.py`, `_observability.py`, `format.py`, and `health.py` from the package root into a new `src/robotsix_auto_mail/core/` sub-package, aligning with the per-module directory convention used by all other modules. All imports updated accordingly across 32 files.
- Move `detect` module under `config/` as `config/detect/` to reflect the conceptual hierarchy (config → auto-discovery). All imports updated: `from robotsix_auto_mail.detect` → `from robotsix_auto_mail.config.detect`.
- Reorganize `mime` module into a per-module package layout (`src/robotsix_auto_mail/mime/__init__.py`) with no import or API breakage.
- Enable baseline periodic mill agents: `test_gap`, `security_posture`, and `module_curator`, via minimal presence files in `.robotsix-mill/periodic/`.
- Extract ``_gather_account_board_data`` and its helpers from ``board.py``
  into a dedicated ``board_data.py`` module, reducing ``board.py`` from 777
  to ~615 lines.
- Consolidate five near-identical single-column UPDATE functions in
  ``db.queries`` into a shared ``_update_column`` helper, reducing ~100
  lines of copy-paste to ~32 while preserving the public API.
- Fix: propagate transient IMAP errors from `_imap_cross_folder_fallback` so callers send 502 instead of silently deleting the local record.
- Add field_validator for method on UnsubscribeDetection model to reject unrecognised values
- Extracted shared OAuth2 setup logic from ``ImapClient`` and ``SmtpClient``
  constructors into the ``_ProtocolClient`` base class, removing 12 lines of
  duplicate boilerplate.
- Extract MIME message construction from `SmtpClient.send()` into a pure `build_plain_text_message()` function in `src/robotsix_auto_mail/mime.py`, making MIME building testable without an SMTP client and reusable by other callers.
- Extract ``_imap_cross_folder_fallback`` and ``_ensure_folder_hierarchy`` shared helpers in ``server/adapters.py``, refactoring the cross-folder IMAP resolution fallback duplicated across ``_handle_delete``, ``_archive_and_delete``, ``_run_batch_delete_background``, and ``_run_batch_archive_background``.
- Extract `_validate_yaml_keys_against_mailconfig` shared helper from duplicated logic in `check_docs_configuration` and `check_docs_connecting` (eliminates 32-line clone detected by jscpd).
- Fix: register missing changelog fragment in `docs/modules.yaml` and fix trailing newline
- Refactor ``_gather_account_board_data`` into six focused helpers: ``_read_account_health``, ``_parse_batch_op``, ``_load_triage_state``, ``_load_archive_context``, ``_load_unsubscribe_suggestions``, and ``_build_record_notes_map``. The main function is now a simple assembly of these calls.
- Removed the observability deprecation shim (`_DeprecatedObservability`, `_ObservabilityLoader`, `_ObservabilityFinder`, and the module-level `__getattr__`) from `robotsix_auto_mail.__init__`. All internal callers now import directly from `robotsix_auto_mail.core._observability` or the top-level re-exports.
- Decomposed `build_parser()` into per-module `register_subparser()` functions so each subcommand's argument definitions live alongside their handlers in the corresponding `commands_*.py` module.
- Update all documentation references from `MAIL_CONFIG_PATH` to `ROBOTSIX_CONFIG_FILE` and update documented default from `config/mail.local.yaml` to `config/config.json`, matching the actual env var name in the code.
- Extend mypy strict checking to cover the test tree with a relaxed override
  (``check_untyped_defs=false``, ``disallow_untyped_defs=false``,
  ``ignore_missing_imports=true``), catching API contract mismatches in tests
  at type-check time without noise from mocks, fixtures, and parametrize
  decorators.
- Fix `scripts/dev/auto-mail-autoupdate.sh` to invoke the autoupdate module via `python -m robotsix_auto_mail.dev.autoupdate` instead of the removed `robotsix-autoupdate` entry point. (mill: `robotsix-autoupdate` CLI entry point removed from pyproject.toml but shell script still calls it (20260704T192757Z-robotsix-autoupdate-cli-entry-point-remo-6151) [WIP])
- Restore ``LLM_API_KEY`` / ``LLM_PROVIDER_MODEL`` env var resolution in ``resolve_llm_api_key()`` and ``resolve_llm_provider_model()`` (the docstrings always claimed ``arg → env_var → config_file`` but the env var step was missing from the implementation).
- Restore ``GET /healthz`` as alias for the liveness endpoint (returns ``{"status":"ok"}``, HTTP 200) — the fleet standard mandates this path and the caretaker HEALTHCHECK probes it.
- Remove `VOLUME /data` directive from Dockerfile (the app writes under `/home/mailbot`, and the anonymous volume created by this directive accumulated orphans on every container recreate).
- Add `.github/workflows/lint-workflows.yml` delegating to the shared `robotsix-github-workflows` reusable workflow for actionlint and zizmor scanning of all workflow files.
- Update implement agent prompt (AGENT.md) to require registering new
  changelog fragments in `docs/modules.yaml` under the `core` module's
  `paths` list.  This eliminates a recurring source of CI noise where
  the implement agent creates a fragment but forgets to register it,
  triggering a `robotsix-modules check-registration` failure and a
  follow-up CI-fix commit.
- Added a three-tier CSS custom property system (`:root` palette → semantic → component tokens) to `board.css`, replacing all 51 hardcoded hex colour literals with `var(--…)` references. No visual change.
- Add `stylelint-declaration-strict-value` plugin to pre-commit stylelint hook, with a rule that requires CSS custom properties (`var(--...)`) for all colour-related declarations (properties ending in `color`, plus `fill` and `stroke`). Board.css is temporarily excluded from this rule until the CSS variable migration is complete.
- Extract shared `force_refresh_token` into `robotsix_auto_mail.oauth2`, removing a 16-line clone between `_imap_force_refresh` and `_smtp_force_refresh`.
- Extract duplicated `_launch_background_worker` call in `_TriageMixin` into a shared private `_launch_triage()` helper method.
- Refactor `card_extra_html` in `MailBoardAdapter`: extract each HTML widget block into its own helper function (`_body_preview_html`, `_notes_indicator`, `_draft_indicator`, `_calendar_indicator`, `_draft_reply_button`, `_delete_form`, `_account_badge`, `_archive_html`, `_move_form`), reducing the method from ~225 lines to a ~40-line flat assembler.
- Updated `docs/architecture.md` to enumerate all triage submodules
  (`_constants`, `agent`, `classifier`, `persistence`, `rules`) with
  one-line descriptions, and to call out `pipeline/_parse.py` as the
  MIME-to-MailRecord converter.
- Replace the duplicated config-key table and YAML example in `docs/connecting.md` with a short link to `docs/configuration.md`, retaining the Trace-ID injection paragraph as its own section.
- Add Contributor Covenant v2.1 Code of Conduct (`.github/CODE_OF_CONDUCT.md`) with enforcement contact matching `SECURITY.md`. Rewrite root `CONTRIBUTING.md` as a gateway document linking to the full development guide, and add Code of Conduct and AI/LLM contribution policy sections to `docs/CONTRIBUTING.md`.
- Add tool-use discipline section to AGENT.md: prefer `read_file` over repeated single-line `run_command` queries (`grep -n`, `sed -n`, `head`, `cat -n`) to reduce observation bloat during codebase exploration.
- Add agent test-edit workflow guidance to AGENT.md: batch test additions, run targeted tests first, full suite last.
- Suppress shellcheck SC2329 false-positive on `cleanup()` in `scripts/server/smoke_board.sh` (function is invoked indirectly via `trap`).
- Remove broker/component-agent integration (clean cutover); rename liveness endpoint ``GET /healthz`` → ``GET /health`` (liveness-only, returns ``{"status":"ok"}`` unconditionally); standardise container image layout: run as user ``app`` (UID 1000) with home ``/home/app``, mount persistent data at ``/data``, add ``VOLUME ["/data"]``, and update the example config's ``store.path`` to ``/data/<id>/mail.db`` for container deployments.
- Remove the broker/component-agent integration (clean cutover): delete ``_component_agent_mixin``, ``_component_agent_responder``, and ``_component_agent_config_contract`` server modules and their tests; drop the ``component_agent_enabled`` config field; remove the ``robotsix-agent-comm`` dependency and the ``calendar``/``broker`` extras from ``pyproject.toml``. Rename the liveness endpoint ``GET /healthz`` → ``GET /health`` (liveness-only, returns ``{"status":"ok"}`` unconditionally). Standardise the container image layout: run as user ``app`` (UID 1000) with home ``/home/app``, mount persistent data at ``/data``, and add ``VOLUME ["/data"]``.
- Drop the bespoke ``pre-commit-autoupdate`` scheduled workflow (``.github/workflows/pre-commit-autoupdate.yml``); let Dependabot manage pre-commit hook updates via the new ``pre-commit`` ecosystem entry in ``.github/dependabot.yml``.
- Add `.mail_log/` to `.gitignore` to prevent runtime log files from being accidentally committed after file-logging removal.
- Drop upper bound on ``requires-python`` (``>=3.14`` instead of ``>=3.14,<3.15``) per fleet standard.
- Remove file-logging support: drop ``log_file_dir`` config field, ``FileHandler`` code, and all ``.mail_log``/``auto-mail-logs`` volume references from Docker/compose/docs.
- Remove ``bandit`` from pre-commit hooks (CI-only scanner per fleet standard).
- Simplify `entrypoint.sh` to the robotsix inverted-entrypoint contract: strip config validation (now owned by the Python application), replace `MAIL_CONFIG_PATH` with `ROBOTSIX_CONFIG_FILE`, and keep only genuine startup work (envsubst templating).
- Adopt towncrier for changelog fragment management: add `[tool.towncrier]` config, `changelog/` directory, towncrier CI check, and release procedure in `CONTRIBUTING.md`.
- Extract `_effective_archive_root` as a `@property` on `_BoardViewMixin`, replacing four identical inline `archive_root` computations (dedup; jscpd clone pair #5).
- Extract the canonical `MailConfig` field→YAML-path map into `robotsix_auto_mail.config._field_map.FIELD_YAML_MAP`, shared by the config contract and `check_config_sync.py` (eliminates the duplicated 29-entry dict; jscpd clone pairs #471–472).
- Split ``tests/server/test_draft_mixin.py`` (841 lines) into two domain-focused modules: ``test_draft_mixin_compute_and_save.py`` and ``test_draft_mixin_send_generate.py``.  The shared ``_DraftMixinFakeHandler`` test helper is now in ``tests/server/_test_helpers.py``.
- Consolidate the `draft` module into `server` as `_draft_generator.py`, since it is exclusively consumed by `_draft_mixin.py`. Move and merge `tests/draft/test_draft.py` into `tests/server/test_draft_mixin.py`. Remove the `draft` module entry from `docs/modules.yaml`.
- Add docstrings to five undocumented private connection/authentication methods in ``ImapClient``: ``__init__``, ``_connect_direct_tls``, ``_connect_starttls``, ``_connect_plain``, ``_authenticate``.
- Add Dependabot auto-merge caller workflow (`.github/workflows/dependabot-auto-merge.yml`) so Dependabot PRs auto-merge once required checks pass.
- Fix Deno install step in CI: authenticate GitHub API call to avoid anonymous rate-limiting 403 errors on shared runners.
- Fix coverage-comment job: produce `.coverage` SQLite data alongside XML so `MERGE_COVERAGE_FILES` has data to combine.
- Fix `scripts/server/smoke_board.sh` to use `ROBOTSIX_CONFIG_FILE` with JSON config (matching `MailAccountsConfig` shape) instead of the deprecated `MAIL_CONFIG_PATH`/YAML format.
- Consolidate `observability` module into `core`: move `src/robotsix_auto_mail/observability/__init__.py` → `src/robotsix_auto_mail/_observability.py` (private module), re-export `setup_logging`, `init_langfuse_tracing`, `setup_observability` from the package root with a deprecation shim for `robotsix_auto_mail.observability`, and move tests to `tests/core/test_observability_{logging,tracing}.py`.
- Triage system prompt now includes a confidence-level rubric defining `low`, `medium`, and `high` so the LLM can calibrate its confidence scores.
- Add a catalog of common mail categories with example triage dispositions to the triage agent's system prompt, helping the LLM classify newsletters, receipts, order confirmations, CI alerts, account notices, and other frequent patterns more consistently.
- Seed new `triage_rules.md` files with commented-out example rules for human users
- Add `--cov-report=xml:coverage.xml` to CI pytest args and a new `coverage-comment` job that posts per-file coverage diff PR comments via `python-coverage-comment-action`
- Fix board unreachable through the central-deploy gateway (502 / "mail.deploy.robotsix.net not working"): the compose `board` service now passes `--host 0.0.0.0` to `serve`, since the default 127.0.0.1 bind is unreachable from other containers.
- Pin first-party `[tool.uv.sources]` git dependencies to commit SHAs instead of `rev = "main"` (agent-comm, board, modules, and the pre-rename robotsix-yaml-config), so a lock refresh can't silently drift or break resolution. Pins are bumped via the automated pin-bump workflow.
- Migrate configuration from ``robotsix-yaml-config`` to ``robotsix-config`` (pydantic + JSON only). ``MailConfig``, ``MailAccount``, and ``MailAccountsConfig`` are now pydantic ``BaseModel`` subclasses (``frozen=True``). Config files use JSON format at ``config/config.json`` (``ROBOTSIX_CONFIG_FILE``); ``config/config.example.json`` is committed. YAML config, env-var fallbacks (``LLM_API_KEY``, ``LLM_PROVIDER_MODEL``, ``MAIL_PASSWORD``), and the ``render_accounts_yaml`` / ``from_yaml`` entry points are removed. A new CI schema-drift check keeps ``config/config.schema.json`` in sync with the model.
- Add `.robotsix-mill/periodic/triage_boilerplate.yaml` presence file to enable the triage-boilerplate periodic workflow.
- Fix ``run_config_sync_agent`` docstring to include ``LLM_API_KEY`` env var in the ``api_key`` resolution chain.
- Fix stale comment in `.robotsix-mill/periodic/config_sync.yaml` — removed reference to non-existent `.env.example`.
- Add unit tests for the health-check module (`tests/core/test_health.py`).
- Update `setup_archive` docstring to document the full API key resolution chain (explicit arg → env var → config file).
- Fixed stale comments and docs: triage-set help now lists all 8 valid actions, db/models.py says "eight kanban columns", ImapClient docstring enumerates all public methods, AGENT.md clarifies LLM_API_KEY/LLM_PROVIDER_MODEL env-var exceptions and fixes stale file paths, CHANGELOG no longer implies MAIL_TRIAGE_RULES_PATH is an env var, and the unused cryptography dev-dep is removed.
- Add robotsix-standards reference link to README.md and AGENT.md
- docs/troubleshooting.md: Fix second row of IMAP/SMTP error table to use YAML dotted-key forms (`imap.tls_mode`/`smtp.tls_mode`) instead of Python dataclass field names.
- Fix stale documentation: remove "env variables" claim from config-loading description in architecture.md, fix component_agent package reference, update ROADMAP.md to not list already-implemented features as future work, remove non-existent test directories from testing.md, add missing CLI subcommands to modules.yaml, and replace dataclass field names with YAML dotted keys in troubleshooting.md
- Fixed stale content in README.md and docs/index.md: removed dead links to nonexistent `docs/decisions/` directory, corrected board column count from four to eight, removed phantom "Add to Calendar" feature description, and replaced "read-only" board description with accurate "kanban board for reviewing and triaging mail".
- Updated `docs/connecting.md`: removed all references to the removed `migrate-config` command, replaced legacy mono-shape YAML examples with valid `accounts:` list-form examples, corrected the error-message description to mention only `detect`, expanded the env-var section to list `LLM_API_KEY` and `LLM_PROVIDER_MODEL`, and corrected the account-selection fallback to describe the `__all__` aggregate view.
- Fix `docs/configuration.md` env-var list to include `LLM_API_KEY` and `LLM_PROVIDER_MODEL`, and remove stale `migrate-config` reference.
- Fix `docs/ingestion.md` to remove phantom env vars (`MAIL_DB_PATH`, `MAIL_IMAP_FOLDER`, `MAIL_INGEST_INTERVAL`) and correct the default DB path to the per-account form (`.data/<account-id>/mail.db`).
- Fix `javascript:` scheme filtering in the mailto unsubscribe link — the `method == "mailto"` branch now requires the `mailto:` prefix like the `header` branch already did, preventing LLM-produced `javascript:` URLs from reaching the board UI.
- Warn when `mail.local.yaml` has lax (group/world-readable) file permissions, suggesting `chmod 600` to protect plaintext credentials.
- Document accepted SSRF risk in `_autoconfig_urls()` with a security comment explaining the rationale.
- Fix silent mail loss after IMAP ``UIDVALIDITY`` changes: ingestion now tracks
  the mailbox's ``UIDVALIDITY`` and, when the server renumbers UIDs (mailbox
  recreated/restored, some server maintenance), resets the stale ``imap_uid``
  watermark so a full ``ALL`` re-scan resumes ingestion (dedup by ``message_id``
  keeps it idempotent). Adds ``ImapClient.select_folder_and_uidvalidity`` and
  ``db.delete_watermark``.
- Switch board HTTP server from `HTTPServer` to `ThreadingHTTPServer` so a slow `/generate-draft` or `/config-sync` request no longer blocks the entire board.)
- Guard `_load_json_watermark` against corrupt JSON and non-dict JSON values
  (e.g. arrays), returning `{}` instead of raising `JSONDecodeError` or
  downstream `AttributeError`.
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
  the YAML config key ``triage.rules_path``. Web-board actions update
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
