# Mail Ingestion

`robotsix-auto-mail` fetches messages from a configured IMAP inbox, parses them
into structured records, and stores them idempotently in a local SQLite
database.

## High-level flow

```
archive setup → connect → fetch → parse → store → update watermark → triage
```

0. **Archive setup** — on the first run, `setup_archive` proposes and creates
   the archive folder hierarchy. This step
   is guarded by `not dry_run and config.archive_enabled` and runs best-effort:
   any failure (LLM, network, IMAP) is logged but never aborts ingestion.
1. **Connect** — open an authenticated IMAP connection using the configured
   credentials and server details.
2. **Fetch** — read the current IMAP UID watermark from the local database.
   Only messages with UIDs greater than the watermark are retrieved from the
   server.
3. **Parse** — each raw MIME message is parsed into a structured record
   (sender, subject, date, recipients, body, attachments).
4. **Store** — each record is inserted into the `mail_records` table. If a
   message with the same `Message-ID` header already exists, it is not
   re-inserted; instead the existing row's `source_folder` and `imap_uid` are
   refreshed and the message counts as a duplicate (see
   [Idempotency](#idempotency)).
5. **Update watermark** — after the full batch has been processed, the
   `imap_uid` watermark is advanced to the highest IMAP UID fetched in the
   batch, regardless of whether each message stored successfully.
6. **Triage** — after the watermark advances, a post-ingest triage pass
   (`run_triage_agent`) classifies the newly-stored inbox mail. This step is
   guarded by `not dry_run and config.triage_on_ingest` and runs best-effort:
   a triage failure is logged but never aborts ingestion or changes the
   stored/skipped counts.

## CLI usage

```sh
$ robotsix-auto-mail ingest
```

A single pass: fetch new mail since the watermark, store it, and exit.

### Watch mode (automatic, on an interval)

```sh
$ robotsix-auto-mail ingest --watch
```

Runs a cycle, then repeats every `ingest.interval_minutes` (config, default
15; override with `MAIL_INGEST_INTERVAL`).  A failed cycle is logged and the
loop continues; Ctrl-C stops it cleanly.  This is the default command of the
`robotsix-auto-mail` Docker service, so `docker compose up -d` keeps the
board's datastore fed automatically.

### Dry-run mode

```sh
$ robotsix-auto-mail ingest --dry-run
```

When `--dry-run` is active the pipeline still fetches messages from IMAP,
parses them, and runs the duplicate check — but it **skips** inserting records
and updating the watermark.  The `Stored` count reflects messages that *would
have been* inserted (i.e. whose `Message-ID` was not already in the database).
A `DRY RUN — nothing stored` banner is printed.

Dry-run is **not** fully side-effect-free, however.  The duplicate-row refresh
(`update_record_source`, see [Idempotency](#idempotency)) runs and commits
*before* the `if dry_run:` store guard, so an already-seen message still has
its existing row's `source_folder` and `imap_uid` updated even in dry-run mode.
The `DRY RUN — nothing stored` banner is therefore slightly misleading for that
case: no *new* record is stored, but an existing one may be refreshed.

### Representative output

```text
Fetched: 12 messages
Stored:  10 new
Skipped:  1 duplicate
Triaged:  4
Errors:   1
  UID 42 (<msg-id@example.com>): failed to parse raw bytes as MIME message
```

### Exit codes

Exit code `0` whenever the pipeline runs to completion, **even when
per-message errors are present**.  Exit code `1` only on configuration-load
failures (missing or invalid config) and fatal connection failures (e.g. IMAP
server unreachable).  This makes the exit code suitable for cron and
automation — a single malformed message will not cause a non-zero exit.

## Datastore schema

The local SQLite database (default: `.data/mail.db`) contains three tables —
`mail_records`, `watermark`, and `triage_decisions` — created automatically on
first run (`CREATE TABLE IF NOT EXISTS`).

### `mail_records` — parsed messages

| Column | Type | Role |
|---|---|---|
| `id` | `INTEGER` | Auto-increment primary key (internal row ID). |
| `imap_uid` | `INTEGER` | IMAP UID of the message on the server at fetch time. |
| `source_folder` | `TEXT NOT NULL DEFAULT 'INBOX'` | The IMAP folder the message was fetched from; refreshed on re-ingest. |
| `message_id` | `TEXT NOT NULL UNIQUE` | The `Message-ID` header value. The `UNIQUE` constraint is the first line of deduplication. |
| `sender` | `TEXT NOT NULL` | `From` header (RFC 5322). |
| `subject` | `TEXT NOT NULL` | `Subject` header, decoded from RFC 2047 if necessary. |
| `date` | `TEXT NOT NULL` | `Date` header parsed to ISO 8601 (empty string if unparseable). |
| `recipients_json` | `TEXT NOT NULL` | JSON object with `"to"` and `"cc"` keys, each an array of address strings. |
| `body_plain` | `TEXT NOT NULL` | Decoded `text/plain` body (empty string if absent). |
| `body_html` | `TEXT NOT NULL` | Decoded `text/html` body (empty string if absent). |
| `attachments_json` | `TEXT NOT NULL` | JSON array of attachment metadata (filename, MIME type, size). |
| `unsubscribe_header` | `TEXT NOT NULL DEFAULT ''` | Raw `List-Unsubscribe` header value (empty string if absent). |
| `status` | `TEXT NOT NULL DEFAULT 'to_read'` | Kanban board column for the record; new mail defaults to `to_read`. |
| `notes` | `TEXT NOT NULL DEFAULT ''` | Free-form user notes attached to the record. |
| `draft_text` | `TEXT NOT NULL DEFAULT ''` | Draft reply text (empty until a draft is composed). |
| `sent_reply_text` | `TEXT NOT NULL DEFAULT ''` | Text of a reply that has been sent (empty until then). |
| `calendar_event_ref` | `TEXT NOT NULL DEFAULT ''` | Reference returned by the calendar agent (or an `error: …` string); empty until "Add to Calendar" is used. |
| `calendar_correlation_id` | `TEXT NOT NULL DEFAULT ''` | Correlation ID of the calendar dispatch request (empty until then). |

### `watermark` — key/value progress store

The `watermark` table is a general key/value store, not a single-row table.
Several keys share it:

| Key | Written by | Meaning |
|---|---|---|
| `imap_uid` | ingestion | Highest IMAP UID fetched so far (the fetch watermark). |
| `account_health` | health probe | Last connection-health status JSON for the account. |
| `archive_structure` | archive setup | JSON list of the chosen archive folder names. |

| Column | Type | Role |
|---|---|---|
| `key` | `TEXT PRIMARY KEY` | The key name (e.g. `imap_uid`, `account_health`, `archive_structure`). |
| `value` | `TEXT NOT NULL` | The stored value for that key (a UID string or a JSON blob). |

On every ingest that fetched at least one message, the `imap_uid` row is
advanced to the maximum UID fetched in the batch — regardless of whether each
individual message stored successfully (an errored message still advances it).

### `triage_decisions` — per-message triage state

Holds the triage classification for each message, keyed by `message_id`
(`FOREIGN KEY` into `mail_records`).  Populated by the post-ingest triage pass
and by manual board actions.

| Column | Type | Role |
|---|---|---|
| `message_id` | `TEXT NOT NULL UNIQUE` | The message's `Message-ID`; foreign key into `mail_records`. |
| `action` | `TEXT NOT NULL CHECK(...)` | The triage action; constrained to the canonical vocabulary (`INBOX`, `HUMAN_TRIAGE`, `PENDING_ACTION`, `TO_ARCHIVE`, `TO_DELETE`, `TO_CALENDAR`, `TO_ANSWER`, `DRAFT_READY`). |
| `source` | `TEXT NOT NULL` | Where the decision came from (e.g. the triage agent vs. a manual action). |
| `reason` | `TEXT NOT NULL DEFAULT ''` | Human-readable rationale for the decision. |
| `confidence` | `TEXT NOT NULL DEFAULT 'medium'` | Confidence level of the decision. |
| `updated_at` | `TEXT NOT NULL` | ISO-8601 timestamp of the last update. |

## Idempotency

The pipeline is safe to re-run even if a previous run crashed partway through.

### Level 1: `Message-ID` uniqueness

The `UNIQUE` constraint on `mail_records.message_id` prevents storing the same
message twice.  A duplicate is **not** a pure no-op, though: when
`_process_messages` finds that a message's `Message-ID` is already present, it
does not re-insert the row, but it *does* call `update_record_source(...)`,
which UPDATEs and COMMITs the existing row's `source_folder` and `imap_uid`
before counting the message as a duplicate.  This keeps a re-ingested message
actionable (for archive/delete) even when its tracked UID has gone stale or it
was re-fetched from a different folder.

### Level 2: IMAP UID watermark

The watermark (`imap_uid` in the `watermark` table) tracks the highest UID
fetched in the batch.  It is advanced to the maximum UID in the batch
regardless of whether each individual message stored successfully — an errored
message still advances it.  Each fetch only retrieves messages with UIDs
strictly greater than this value, so messages from completed batches are never
re-fetched.

### Crash recovery scenarios

- **Crash before watermark update:** Stored messages have their `message_id`
  recorded.  On re-run the same UIDs are re-fetched (watermark hasn't moved),
  but the `UNIQUE` constraint causes them to be skipped as duplicates.
- **Crash after watermark update:** The watermark has already advanced;
  subsequent runs start from the new watermark and never re-fetch those UIDs.
- **Empty batch:** If no new messages exist, the pipeline returns immediately
  without touching the watermark.

## Configuration

The `ingest` subcommand uses the same IMAP connection and authentication
settings as the rest of `robotsix-auto-mail` (see
[docs/connecting.md](connecting.md)).  Two additional keys control the local
datastore and the watch interval:

| Variable | YAML key | Default | Purpose |
|---|---|---|---|
| `MAIL_DB_PATH` | `store.path` | `.data/mail.db` | Filesystem path to the SQLite database |
| `MAIL_IMAP_FOLDER` | `imap.folder` | `INBOX` | IMAP mailbox folder to fetch from |
| `MAIL_INGEST_INTERVAL` | `ingest.interval_minutes` | `15` | Minutes between cycles in `--watch` mode |

The database is created automatically on first use — no manual setup is needed.

## Log events and monitoring

The `ingest_mail()` function emits structured log events that are useful for
monitoring and debugging. The most important one is the final `batch_summary`
line, which is logged at the end of a run and carries metrics about what
happened.

### The `batch_summary` log line

`batch_summary` is **not** emitted on every run.  When no new messages are
fetched (`total_fetched == 0`) the pipeline returns early — before the summary
is logged — so no `batch_summary` line appears.  In `--watch` mode, where most
cycles fetch nothing, this is the common case.  When at least one message is
fetched, exactly one `batch_summary` line is logged per run (one per cycle in
watch mode).

The metrics are **not** discrete structured fields.  `batch_summary` is logged
as a single printf-style message string, and the JSON log formatter
(`logging.format: json`) emits only the generic record envelope:
`timestamp`, `level`, `logger`, `message`, and `trace_id`.  All of the
ingestion metrics live *inside* the `message` string — there is no `event` key
and no per-metric JSON field.

**Metrics embedded in the `message` string:**

| Token | Type | Meaning |
|---|---|---|
| `total_fetched` | int | Number of messages retrieved from IMAP since the watermark. |
| `stored` | int | Number of messages newly inserted into `mail_records`. |
| `skipped` | int | Number of messages skipped as duplicates (same `Message-ID` already in database). |
| `error_count` | int | Number of per-message errors (parse failures, DB write errors, etc.). |
| `triaged` | int | Number of triage decisions produced by the post-ingest triage pass (0 when triage is disabled, in dry-run mode, or when triage raised an error). |
| `duration_ms` | float | Wall-clock duration of the ingestion run in milliseconds, measured using `time.perf_counter()` (monotonic, robust against system clock adjustments). Useful for detecting slow IMAP servers or degraded LLM performance. |

**Example** (JSON log output with `logging.format: json`):

```json
{
  "timestamp": "2025-06-14T12:34:56.789012Z",
  "level": "INFO",
  "logger": "robotsix_auto_mail.pipeline",
  "message": "batch_summary total_fetched=5 stored=4 skipped=1 error_count=0 triaged=2 duration_ms=1234.5",
  "trace_id": "00000000000000000000000000000000"
}
```

**Operational value:** The `duration_ms` field is the single most useful signal
for detecting operational problems in a cron or systemd one-shot ingestion job.
A sudden spike often indicates a slow IMAP server (e.g. due to backlog or
network issues), degrading LLM-assisted triage performance, or other resource
contention. Monitoring this field allows early detection of such problems.
