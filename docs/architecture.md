# Architecture

This document is the internal, structural view of `robotsix-auto-mail`:
how the package is laid out, which modules own which responsibilities, and
how the runtime pieces call one another.  For the user-facing description of
mail ingestion see [docs/ingestion.md](ingestion.md); for configuration keys
and precedence see [docs/connecting.md](connecting.md).

## Package layout

The project follows the `src` layout prescribed by
[ADR 0001](decisions/0001-programming-language.md):

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
holds the five common config fields (host, port, tls_mode, username,
password) and the `_dispatch_tls()` dispatch loop.  Two concrete subclasses
implement the protocol-specific steps:

- `imap/` — a stdlib `imaplib` wrapper (`imap/client.py`).
- `smtp/` — a stdlib `smtplib` sending client.

### Ingestion

- `pipeline/` — orchestrates fetch → parse → store → watermark; includes
  watermark-aware IMAP fetch logic.
- `parser/` — MIME to `MailRecord` parsing.

### Datastore

- `db/` — SQLite schema, the `MailRecord` type, insert, and watermark
  read/write.
- `server/views/board.py` and `triage/persistence.py` — the mail-processing
  status read/write layer for the kanban board.

### Configuration

- `config/__init__.py` — loads `MailConfig` from YAML and environment.
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

### Surfaces

- `cli/` — the CLI entry point exposing the subcommands.
- `server/` — the HTTP kanban board server.

## Ingestion data flow

`pipeline.ingest_mail()` orchestrates a single ingest pass in this order:

1. On the first run (and only when not in dry-run mode and
   `config.archive_enabled` is set), `archive.setup_archive()` proposes and
   persists the archive folder layout.
2. `fetch.fetch_new_messages()` reads the `imap_uid` watermark via
   `db.get_watermark()` and issues `UID SEARCH` / `UID FETCH BODY.PEEK[]` for
   messages with UIDs greater than the watermark.
3. For each fetched message, `parser.parse_message()` produces a `MailRecord`.
4. `db.record_exists()` checks the `Message-ID` for deduplication; known
   messages are counted as duplicates and skipped.
5. New records are stored with `db.insert_record()` (skipped under
   `--dry-run`).
6. After the batch, `fetch.update_watermark()` advances the watermark to the
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
exception types, then authenticate via `_authenticate()`.  See
[docs/troubleshooting.md](troubleshooting.md) for the resulting error
hierarchy and how to diagnose each failure.

## Configuration resolution

`MailConfig` is loaded from a YAML config file and environment variables
through a single cascade — built-in defaults → YAML file → environment
variables, applied field by field.  The full precedence rules and the
complete key table are documented in [docs/connecting.md](connecting.md);
this document does not restate them.

## CLI and board surfaces

`cli/` exposes the subcommands (`probe`, `ingest`, `board`, `serve`,
`detect`, `triage`, `config-sync`, and their `-set` companions).  `server/`
serves the read/write kanban board over HTTP, backed by the same SQLite
datastore.
