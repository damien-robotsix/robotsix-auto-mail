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
  watermark-aware IMAP fetch logic and MIME-to-`MailRecord` parsing.

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
  that builds `MailConfig` from built-in defaults and the YAML file.
  The `MAIL_CONFIG_PATH` env var locates the config file;
  `LLM_API_KEY`/`LLM_PROVIDER_MODEL` serve as env-var fallbacks for LLM
  calls specifically (not a general config override tier).
- `config/model.py` — the config dataclasses (`MailConfig` and friends).
- `config/schema.py` — the field defaults and specs the cascade applies.
- `config/config_sync_agent.py` — the optional LLM-driven config-drift advisory
  agent.

### Provider detection

- `detect/` — MX-record / autoconfig / LLM provider detection and
  auto-configuration lookup.

### Archive layout

- `db/archive.py` — the self-managed archive folder structure, with a first-run
  LLM layout proposal remembered via the `watermark` table.

### LLM-driven agents

- `triage/` — the inbox triage classifier.
- `config/config_sync_agent.py` — the config-drift advisory agent (also listed
  above).
- `draft/` — `generate_draft_reply`, the draft-reply generator tied to the
  `TO_ANSWER` triage action; it persists the draft via `db.update_draft_text`
  (no mail is sent).
- `server/_component_agent_responder.py` and
  `server/_component_agent_mixin.py` — the board server's component HTTP
  API (`monitor` / `config-get` / `config-set`), implemented across two
  server-package files.

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
`MAIL_CONFIG_PATH`, default `config/mail.local.yaml`), with any omitted
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
