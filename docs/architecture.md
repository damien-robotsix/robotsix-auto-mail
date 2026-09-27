# Architecture

This document is the internal, structural view of `robotsix-auto-mail`:
how the package is laid out, which modules own which responsibilities, and
how the runtime pieces call one another.  For the user-facing description of
mail ingestion see [docs/ingestion.md](ingestion.md); for configuration keys
and precedence see [docs/connecting.md](connecting.md).

## Package layout

The project follows the `src` layout:

| Path | Role |
|---|---|
| `src/robotsix_auto_mail/` | The production Python package. |
| `tests/` | Test code mirroring the package, one subdirectory per module. |
| `config/` | Example/sample configuration for operators. |
| `docs/` | Documentation, the module taxonomy, and architecture decisions. |

The canonical inventory of every module and the files it owns lives in
[docs/modules.yaml](modules.yaml).  The file is validated at CI time by the
`robotsix-modules check-registration` command.

## Module map

The runtime modules group into logical layers.

### Protocol clients

`imap/_protocol.py` defines the shared `_ProtocolClient` abstract base, which
holds the common config fields (host, port, tls_mode, username, password)
plus the OAuth2 fields (oauth2_token, oauth2_client_id,
oauth2_client_secret) and an optional dynamic token provider, alongside the
`_dispatch_tls()` dispatch loop.  Two concrete subclasses implement the
protocol-specific steps:

- `imap/` — a stdlib `imaplib` wrapper (`imap/client.py`).
- `smtp/` — a stdlib `smtplib` sending client.

### OAuth2 / Microsoft 365 (XOAUTH2)

Microsoft 365 rejects password-based IMAP/SMTP auth and instead requires an
OAuth2 access token presented over SASL XOAUTH2:

- `oauth2/` — the MSAL-backed token provider for Microsoft 365.  It runs
  the device-code consent flow once, persists the MSAL token cache in the
  per-account data folder, and hands out fresh access tokens via silent
  refresh thereafter.  `msal` is an optional dependency (the
  `robotsix-auto-mail[microsoft]` extra) imported lazily.
- The `auth login` CLI subcommand drives that device-code login for an
  account.
- `build_xoauth2_response` in `imap/_protocol.py` formats the SASL XOAUTH2
  wire string the IMAP and SMTP clients send when a token is present.

### Ingestion

- `pipeline/` — orchestrates fetch → parse → store → watermark; includes
  watermark-aware IMAP fetch logic and MIME-to-`MailRecord` parsing
  (`_parse.py`).

### Datastore

- `db/` — SQLite schema, the `MailRecord` type, insert, and watermark
  read/write.
- `db/queries.py` — the status-write helpers (`update_notes`,
  `update_draft_text`, `update_record_source`, `update_sent_reply_text`,
  and the calendar updaters) against the `mail_records` table.
- `triage/persistence.py` — CRUD for the `triage_decisions` table (the
  per-message triage action and its source).
- `server/views/board.py` — a render/view layer that only READS records
  (via `db.list_records`) to build the kanban-board HTML; it performs no
  status writes.

### Configuration

- `config/__init__.py` — re-exports the config API (loaders, dataclasses,
  and schema) for callers.
- `config/loader.py` — the actual load cascade (`load` / `load_accounts`)
  that builds `MailConfig` from built-in defaults and the config file, plus
  the component-wide LLM resolvers (`resolve_llm_api_key`,
  `resolve_llm_provider_model`, `load_langfuse`).  The `ROBOTSIX_CONFIG_FILE`
  env var locates the config file and is the only environment variable
  consulted — there is no env-var override tier.
- `config/credentials.py` — the canonical `langfuse` / `openrouter` credential
  blocks fixed by robotsix-standards, keyed by LLM-function alias.
- `config/model.py` — the config dataclasses (`MailConfig` and friends).
- `config/schema.py` — the field defaults and specs the cascade applies.
- `config/config_sync_agent.py` — the optional LLM-driven config-drift advisory
  agent.

### Provider detection

- `detect/` — MX-record / autoconfig / LLM provider detection and
  auto-configuration lookup.

### Archive layout

- `db/archive.py` — the self-managed archive folder structure, with a first-run
  LLM layout proposal remembered via the `watermark` table. IMAP folders are
  created **lazily** when a message is archived into a destination, not during
  setup. Includes a cleanup routine that removes empty subfolders bottom-up
  during reconcile.

### LLM-driven agents

- `triage/` — the inbox triage classifier:
  - `triage/_constants.py` — triage action constants (`INBOX`, `TO_ARCHIVE`,
    `TO_CALENDAR`, …), canonical board order, and watermark keys.
  - `triage/agent.py` — `run_triage_agent`: builds LLM prompts, drives inbox
    classification, persists results, and runs post-classification tasks
    (archive subfolder hints, unsubscribe detection).
  - `triage/classifier.py` — deterministic and LLM-based archive subfolder
    proposals, with user-override storage via watermarks.
  - `triage/persistence.py` — Pydantic output-contract models and SQLite CRUD
    helpers for the `triage_decisions` table.
- `config/config_sync_agent.py` — the config-drift advisory agent (also listed
  above).

### Surfaces

- `cli/` — the CLI entry point exposing the subcommands.
- `server/` — the HTTP kanban board server.

### Calendar surface

- `TO_CALENDAR` is one of the triage actions (`triage/_constants.py`),
  rendered as the "To calendar" board column.
- Each `mail_records` row carries two calendar columns,
  `calendar_event_ref` and `calendar_correlation_id`, written by
  `update_calendar_event_ref` and `update_calendar_correlation_id` in
  `db/queries.py`.
- `server/views/detail.py` reads `calendar_event_ref` in
  `_render_calendar_feedback` to show, in the mail detail view, whether a
  calendar event has been recorded for the message.

## Ingestion data flow

`pipeline.ingest_mail()` orchestrates a single ingest pass in this order:

1. On the first run (and only when not in dry-run mode and
   `config.archive_enabled` is set), `archive.setup_archive()` proposes and
   persists the archive folder layout.
2. `pipeline.fetch_new_messages()` reads the `imap_uid` watermark via
   `db.get_watermark()` and issues `UID SEARCH` / `UID FETCH BODY.PEEK[]` for
   messages with UIDs greater than the watermark.
3. For each fetched message, `parse_message()` produces a `MailRecord`.
4. `db.record_exists()` checks the `Message-ID` for deduplication; known
   messages are counted as duplicates and skipped.
5. New records are stored with `db.insert_record()` (skipped under
   `--dry-run`).
6. After the batch, `pipeline.update_watermark()` advances the watermark to the
   highest UID seen (skipped under `--dry-run`).

See [docs/ingestion.md](ingestion.md) for the user-facing description,
datastore schema, and idempotency guarantees — this document does not
duplicate them.

## Protocol-client design

Both clients inherit `_ProtocolClient`.  Its `_dispatch_tls()` method
dispatches on `tls_mode` to one of three abstract connection helpers:

| `tls_mode` | Helper |
|---|---|
| `direct-tls` | `_connect_direct_tls` |
| `starttls` | `_connect_starttls` |
| `none` | `_connect_plain` |

An unrecognised mode raises `ValueError`.  The IMAP and SMTP subclasses
provide the concrete connection steps with their own protocol libraries and
exception types, then authenticate via `_authenticate()` — using the SASL
XOAUTH2 response built by `build_xoauth2_response` when an OAuth2 token (or
token provider) is configured, and password auth otherwise.  See
[docs/troubleshooting.md](troubleshooting.md) for the resulting error
hierarchy and how to diagnose each failure.

## Configuration resolution

`MailConfig` is loaded from a single YAML config file (located via
`ROBOTSIX_CONFIG_FILE`, default `config/config.json`), with any omitted
field falling back to its built-in default.  The complete key table is
documented in [docs/connecting.md](connecting.md); this document does not
restate it.

## CLI and board surfaces

`cli/` exposes the subcommands (`probe`, `ingest`, `board`, `serve`,
`detect`, `config-sync`, `triage`, `triage-set`, `config-sync-set`,
and `auth` — with its `auth login` sub-subcommand for the
OAuth2 device-code flow).  Only `triage` and `config-sync` have `-set`
companions.  `server/` serves the read/write kanban board over HTTP, backed
by the same SQLite datastore.

## Server request handling: composition model

The `server/` request-handling layer follows a **composition** design: a thin
HTTP-infrastructure shell (`BoardHandler`) dispatches every endpoint to a
stateless *service* through a per-request *context*.  This replaced an earlier
~15-way mixin inheritance (`BoardHandler(AttachmentMixin, MailboxMixin, …,
BaseHTTPRequestHandler)`); that migration is now **complete** — no mixin
modules remain and `BoardHandler` inherits only from
`http.server.BaseHTTPRequestHandler`.

### `BoardHandler` — HTTP infra + data-driven routing

`server/handlers.py` defines `BoardHandler`, which owns only the
cross-cutting HTTP concerns:

- the response sinks and transport helpers (`_send_response`, `_redirect`,
  `_not_found`, `_bad_request`, `_problem`, `_serve_json`);
- per-request account selection (`_select_account`) and the per-request
  state it sets (`_current_account_id`, `_aggregate`, `_account_cookie`);
- the shared archive guards (`_effective_archive_root`,
  `_require_imap_configured`, `_validate_archive_path`); and
- the **route tables** in `do_GET` / `do_POST` (`do_PUT` where present).

The GET table is an ordered list of `(predicate, handler)` tuples (first
match wins, so prefix routes are ordered after their more-specific
siblings); the POST table is an exact-match `dict` preceded by a few
prefix-based special cases.  A handful of cross-account endpoints
(`/auth-status`, `/add-account`, `/config`, `/delete-account`,
`/config/rollback`, …) are dispatched *before* `_select_account()` so they
work regardless of — or in the absence of — a session account.  Almost every
route resolves to `self._services.get(SomeService).handle_…(ctx)`.

### Services — stateless endpoint groups

Each former mixin is now a `server/_<name>_service.py` module whose class
subclasses `Service` (`server/_services.py`).  A `Service` stores only the
injected, request-independent dependencies (`db_path`, `mail_config`,
`accounts`) — never per-request state — so a single instance is safe to reuse
across every request on a connection.  The `ServiceContainer` (also in
`server/_services.py`) is built once per `make_board_handler()` call, wired
with those dependencies, and exposed as `self._services`; its `get()` lazily
instantiates and memoises each service on first use.

### `RequestContext` — the structural contract

Services never depend on the concrete `BoardHandler`.  They depend on the
narrow `RequestContext` structural `Protocol` defined in
`server/_board_handler_protocol.py` (an alias for `BoardHandlerProtocol`).
The running handler *is* the context — it structurally satisfies the Protocol
— so `do_GET` / `do_POST` pass `cast("RequestContext", self)` to each service
method.  The Protocol exposes the HTTP infra, the injected dependencies, the
per-request transport (`path`, `headers`, `rfile`, `server`), the per-request
state, and the archive guards listed above.

### Shared request helpers

Two request-handling steps are shared by every endpoint group and therefore
live in `server/_request_helpers.py` rather than on any one service:

- `parse_request_body(ctx, *fields, no_strip=…)` — parse the body as
  URL-encoded form data with a JSON fallback;
- `handle_post_action(ctx, *fields, action=…, …)` — the shared POST skeleton
  (parse body → validate `message_id` → open a read-only DB connection and
  look up the record → delegate to `action` → safe redirect).

`launch_background_worker(ctx, …)` (single-flight watermark guard + optional
daemon thread) is exposed on the context as `_launch_background_worker`.

### Adding or migrating an endpoint

To add a new endpoint group (the same recipe that drove the mixin
migration):

1. Create `server/_<name>_service.py` with a `Service` subclass; each
   endpoint method takes `ctx: RequestContext` as its first argument and
   reads all handler-owned state off `ctx`.
2. Register the class in the `handlers.py` imports and dispatch to it via
   `self._services.get(<Name>Service).handle_…(ctx)` from the route table.
3. Register the new module in [docs/modules.yaml](modules.yaml) and add
   `_FakeHandler`-style unit tests that instantiate the service directly and
   pass a stub context.

The service container itself needs no per-service registration — `get()`
builds any `Service` subclass on demand from the injected dependencies.
