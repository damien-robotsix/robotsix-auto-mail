# robotsix-auto-mail

robotsix-auto-mail fetches mail over IMAP, parses it, and stores it in a
local SQLite datastore, with an HTTP kanban board for review.

## PyPI packaging metadata

The `pyproject.toml` (repo root) includes standard PyPI metadata:

- **`license = "MIT"`** — matches the `LICENSE` file at repo root.
- **`classifiers`** — `Development Status :: 4 - Beta`, `License :: OSI Approved :: MIT License`, `Programming Language :: Python :: 3.14`, `Operating System :: OS Independent`.
- **`keywords`** — `["mail", "imap", "smtp", "triage", "kanban"]`.
- **`[project.urls]`** — `Homepage`, `Repository`, `Documentation`, `Issues`, `Changelog` — all pointing to the GitHub repo and GitHub Pages docs site.
- **`CHANGELOG.md`** at repo root documents unreleased changes.

## Archive feature

robotsix-auto-mail manages its own archive folder hierarchy, independent of
any pre-existing mailbox layout. On the first run a quick LLM call proposes
an appropriate layout based on the mailbox's existing folders; the chosen
structure is then remembered so subsequent runs reuse it without re-asking
the LLM. The implementation lives in `src/robotsix_auto_mail/db/archive.py`
(module `db.archive`) and is wired into ingestion from
`src/robotsix_auto_mail/pipeline/__init__.py` (the `pipeline` package).

### How the LLM determines the structure

On the first run, `setup_archive(conn, client, *, archive_root=...,
api_key=..., tier=Tier.CHEAP)` lists the mailbox's existing folders and
calls `determine_archive_structure(...)`. That function builds an
`OpenRouterDeepseekProvider` (deepseek) agent at the `Tier.CHEAP` tier and,
via `pydantic_ai.PromptedOutput`, asks the LLM to return an
`ArchiveStructure` — a Pydantic model with a single
`folders: list[str]` field. Each entry is a sub-path relative to the
archive root, using `/` as the hierarchy separator (the list may be empty,
meaning just the root).

The instructions come from `_build_archive_system_prompt(archive_root)`,
which tells the model to return only a JSON object with a `folders` list of
sub-paths relative to the root — no root prefix, no prose, no markdown
fences.

### Where the choice is persisted and how retrieval works

The proposed structure (the full list of archive folder names) is stored as
JSON in the generic `watermark` key-value table under the key
`archive_structure` (the `_ARCHIVE_WATERMARK_KEY` constant) via
`set_watermark`. On subsequent runs `get_watermark` returns the cached JSON
and `setup_archive` short-circuits immediately — returning the remembered
list without listing folders, calling the LLM, or creating anything.

### First-run workflow vs. cheap no-op on later runs

- **First run** (no persisted structure) with a resolvable API key: the LLM
  proposes a layout, the missing folders are created, and the resulting
  full-name list is persisted to the watermark.
- **Later runs**: the watermark hit short-circuits `setup_archive`, making
  it a cheap no-op with no LLM call.

A key consequence (a deliberate repo convention): because of the watermark
short-circuit, once a structure is persisted, later changes to the root
path or other archive config do **not** re-trigger the LLM proposal — the
structure is remembered by design.

### No-API-key fallback

If no API key is resolvable, `setup_archive` does not call the LLM and does
not error. It falls back to a root-only structure (just `archive_root`), so
ingestion is never blocked by a missing key.

### Configuration options

Both fields live on `MailConfig` (`src/robotsix_auto_mail/config/__init__.py`):

- **`archive_root`** — env `MAIL_ARCHIVE_ROOT`, YAML `archive.root`.
  Defaults to `robotsix-mail-archive` (the `DEFAULT_ARCHIVE_ROOT`
  constant). Override it via the environment variable or the
  `config/mail.local.yaml` config file.
- **`archive_enabled`** — env `MAIL_ARCHIVE_ENABLED`, YAML
  `archive.enabled`. Defaults to `True`. This is the disable toggle for the
  archive feature.

### Pipeline integration

`setup_archive` is invoked from `ingest_mail` in `pipeline/__init__.py` (the `pipeline` package) as a
first-run step, guarded by `not dry_run` and `config.archive_enabled`. The
call is wrapped in a best-effort `try`/`except` that logs the failure (via
`_logger.exception`) but does not propagate it — an archive failure
(LLM, network, or IMAP) never aborts ingestion. Because `setup_archive`
only persists its watermark on success, a failed run naturally retries on
the next ingestion.

## Calendar (Add to Calendar)

The 'Add to Calendar' action lets a user send an email's date/time
references to the ``robotsix-calendar`` agent for event creation, directly
from the mail detail view in the web board.

### How the dispatch works

1. The detail view renders an **Add to Calendar** button (see
   `_render_add_to_calendar_button` in `server/views/detail.py`).
2. Clicking the button fires a JS `confirm()` dialog showing a calendar
   summary (`extract_calendar_summary` from `calendar.py`).
3. On confirmation, `window.addToCalendar(payload)` in
   `board-auto-mail.js` sends a `POST /add-to-calendar` with JSON body.
4. The server-side handler (`_CalendarMixin._handle_add_to_calendar` in
   `server/_action_mixin.py`) calls `dispatch_calendar_request` from
   `calendar.py`.
5. `dispatch_calendar_request` builds a `CalendarEventRequest` Pydantic
   model, then calls `Agent.send_notification(recipient="robotsix-calendar",
   body=...)` — a **fire-and-forget** send via the `robotsix_agent_comm`
   message bus.  No response is awaited.

### Message format (inter-agent contract)

`CalendarEventRequest` (defined in `calendar.py`) carries:

| Field | Type | Purpose |
|---|---|---|
| `correlation_id` | `str` | Unique identifier for request/response lifecycle |
| `message_id` | `str` | The original email's Message-ID |
| `subject` | `str` | Email subject line |
| `sender` | `str` | Email sender address |
| `body_text` | `str` | Plain-text body of the email |
| `email_date` | `str` | ISO-8601 timestamp of the email |
| `extracted_dates` | `list[str]` | Date/time references extracted via `DATE_TIME_RE` from `extract_dates_from_body` |

The `extract_calendar_summary` helper builds a human-readable summary string
for the confirmation dialog.

### UI: the detail view button

The button is rendered by `_render_add_to_calendar_button` in
`server/views/detail.py`.  It appears in a "Calendar" field row in the
detail view.

- When `calendar_event_ref` is empty (no response yet): active button with
  `data-calendar-payload` / `data-calendar-summary` attributes and an inline
  `onclick` handler that calls `confirm()` then `window.addToCalendar()`.
- When `calendar_event_ref` holds a success (non-empty, not starting with
  `"error: "`): the button is **disabled** and relabeled "Calendar event
  created".
- A `calendar-feedback` span renders success/error indicators below the
  button.

The JS dispatch lives in `server/static/board-auto-mail.js` (`addToCalendar`
function) — unchanged by this ticket.

### Configuration

- **`calendar.enabled`** (env `MAIL_CALENDAR_ENABLED`): `bool`, default
  `True`.  Per-account field (NOT a top-level section like `board_agent`).
  Each account can independently enable/disable the calendar action.

### Enabling / disabling

Default enabled.  Set `calendar.enabled: false` in the YAML config (or
`MAIL_CALENDAR_ENABLED=false` in the environment) to hide the button and
suppress dispatch entirely.

### Graceful degradation

When the `robotsix_agent_comm` package is not installed and the feature is
enabled, the `POST /add-to-calendar` handler catches `CalendarDispatchError`
and returns HTTP 503 with an error message.  The button still appears
(unless disabled) but the user sees an error alert from the JS error
handler.

When `calendar_enabled=False`, the button is not rendered at all — no
dependency is needed.

## Project layout

Static assets (CSS, JS, images) for the web board template live in
`src/robotsix_auto_mail/server/static/` and are loaded at module level via
`Path(__file__).parent / "static" / "<filename>").read_text()`.  Do
**not** embed CSS or JS as Python string literals in the `server/` package —
the separation keeps the server module navigable and allows CSS/JS
tooling (linting, syntax highlighting, validation) to apply.

The canonical example is the board stylesheet (lines 21–28 of `server/_constants.py`):
```python
_STATIC_BOARD_CSS = (
    importlib.resources.files("robotsix_board") / "static" / "board.css"
).read_text()
_STATIC_AUTOMAIL_BOARD_CSS = (
    importlib.resources.files("robotsix_auto_mail.server") / "static" / "board.css"
).read_text()
```

## Documentation conventions

When you add or change a user-facing CLI subcommand in
`src/robotsix_auto_mail/cli/__init__.py` (the `cli` package), document it in `docs/connecting.md` in the
same PR, following the `config-sync` command section pattern (purpose,
optional-extra requirements, flags, example invocation, and output).

## Testing conventions

**Rule:** When a test file exceeds ~500 lines and has clear thematic
sections (separated by `# -----` or `# =====` comment blocks), split it
into domain-focused modules under the same directory. One module per
endpoint, handler mixin, or logical concern.
