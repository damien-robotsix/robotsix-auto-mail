# Connecting

`robotsix-auto-mail` needs IMAP and SMTP connection parameters. They are
resolved through a single, predictable cascade:

**built-in defaults → a YAML config file → environment variables.**

Each layer overrides the one before it, field by field. You can supply
everything in the YAML file, everything via `MAIL_*` environment variables,
or mix the two (e.g. host/username in the file, password via `MAIL_PASSWORD`).

New users can also run `robotsix-auto-mail detect` to auto-generate the YAML
file from just an email address — see [Auto-detection with
`detect`](#auto-detection-with-detect).

## Quick start — Docker Compose (recommended)

The project includes a `docker-compose.yml` that builds the container and
mounts configuration without rebuilding the image.

```sh
# 1. Create your local config from the template
cp config/mail.local.example.yaml config/mail.local.yaml

# 2. Edit it with your real credentials
$EDITOR config/mail.local.yaml

# 3. Build the image
docker compose build

# 4. Run commands via `docker compose run`
docker compose run robotsix-auto-mail probe
docker compose run robotsix-auto-mail ingest
docker compose run robotsix-auto-mail board
```

### How it works

- `config/mail.local.yaml` *(git-ignored)* holds your settings — typically
  `imap.host`, `smtp.host`, `auth.username`, and `auth.password`. Any field
  you omit falls back to its built-in default.
- The `./config` directory is bind-mounted into the container at
  `/home/mailbot/config`, so editing `config/mail.local.yaml` on the host
  is picked up immediately — no rebuild needed.
- The `MAIL_CONFIG_PATH` environment variable is set to
  `/home/mailbot/config/mail.local.yaml` by `docker-compose.yml`.
- The mail database persists in `./.mail_data` on the host (bind-mounted,
  git-ignored).

## Auto-detection with `detect`

Instead of manually researching and writing config, you can auto-generate it
from just an email address. The `detect` command resolves the IMAP/SMTP
settings through a ladder, most authoritative first:

1. **Published autoconfig** — the Mozilla ISPDB and the domain's own
   `autoconfig.<domain>` endpoint.
2. **MX records** — a DNS-over-HTTPS lookup identifies the hosting provider
   from the domain's mail servers (e.g. `*.mail.ovh.net` → OVH), mapped to
   that provider's known IMAP/SMTP settings.
3. **LLM** — only if the first two miss; the MX hostnames are passed in as a
   hint so it identifies the provider rather than guessing blindly.

After writing the config, `detect` verifies it by connecting (see below), and
refines on failure. The LLM step needs a `pydantic-ai` installation and an
API key; autoconfig and MX detection do not.

### Setup

```sh
# Installs dev dependencies (incl. pydantic-ai) from the committed uv.lock,
# so you get the exact same resolved versions as CI. The dev tooling lives
# in the `dev` extra, which `--extra dev` pulls in. After changing
# dependencies in pyproject.toml, run `uv lock` and commit the updated
# uv.lock.
uv sync --extra dev

# Set your OpenRouter API key (required)
export LLM_API_KEY=sk-or-v1-…
```

Instead of an environment variable, you can put this in the `llm:` section of
`config/mail.local.yaml` (see [Configuration keys](#configuration-keys)). The
LLM credentials resolve through the same cascade as everything else — the
`LLM_API_KEY` environment variable overrides the file. The same
settings will be reused by future LLM-assisted mail processing, not just
`detect`.

### Minimal usage

```sh
robotsix-auto-mail detect user@gmail.com
```

This auto-detects settings, prompts for the password interactively, writes a
multi-account `config/mail.local.yaml` (a top-level `default_account:` plus an
`accounts:` list with one entry) with the password included, and then verifies
the settings by connecting to the IMAP and SMTP servers (the same check as the
`probe` command). Pass `--no-verify` to skip that connection check.

The detected account's `id` is derived from the email address (a sanitised
`local-part-domain` form) unless you pass `--id <id>` explicitly; its store
defaults to the per-account folder `.data/<id>/mail.db`, and `default_account`
is set to that id when the file is new.

**Appending accounts.** Re-running `detect` against an existing multi-account
file **appends** the newly-detected account, preserving the other accounts
already in the file. If the resolved `id` already exists, `detect` refuses
(exit 1) rather than clobbering it — pass `--id <new-id>` to add a distinct
account. If the output file is still in the single-account ("mono") shape —
which is no longer supported at runtime — the existing configuration is
converted to a `default` account before the new one is appended (run
`migrate-config` first if you prefer to convert it explicitly).

### Scripting usage

```sh
robotsix-auto-mail detect user@gmail.com \
    --password "app-password" \
    --output config/mail.local.yaml
```

### Options

| Option | Required | Default | Purpose |
|---|---|---|---|
| `EMAIL` (positional) | yes | – | Email address to detect settings for |
| `--id ID` | no | (derived from email) | Account id for the detected account (the `accounts:` entry id and `.data/<id>/mail.db` store folder) |
| `--password` | no | (prompted) | Password to write into the config file |
| `--output PATH` | no | `config/mail.local.yaml` | Write mail config to this path |
| `--stdout` | no | – | Print config to stdout instead of writing to file; password is intentionally omitted (must be filled in manually or via `MAIL_PASSWORD`); no verification is performed |
| `--no-verify` | no | – | Skip the post-write IMAP/SMTP connection check |

### Docker invocation

```sh
# Set your OpenRouter API key (or put it in the config file's llm: section)
export LLM_API_KEY=sk-or-v1-…

# Detect provider settings, write config, and verify connectivity —
# all in one step (prompts for the password; uses the run TTY).
docker compose run robotsix-auto-mail detect user@gmail.com
```

The `detect` command writes `config/mail.local.yaml` (with the password
included when one is supplied) into the bind-mounted `./config` directory on
the host.  No image rebuild is needed — the file is available immediately.

The `--password` flag works the same as in native mode.  When omitted, an
interactive prompt appears (requires a TTY — use `docker compose run` without
`-T`).

### Caveats

- **LLM output can be wrong.** That is exactly why `detect` verifies by
  connecting after writing the config. If verification fails, edit
  `config/mail.local.yaml` and re-run `probe`.
- With `--no-verify` (or `--stdout`, which never writes), no connection is
  made — `detect` is then purely a config-file generator, so run
  `robotsix-auto-mail probe` yourself afterwards.
  When using `--stdout`, the password is intentionally omitted from the printed
  config for security (to avoid leaking it into shell history or logs). You must
  supply the password separately: save the printed config to a file and edit it
  to fill in `auth.password`, or supply the password via the `MAIL_PASSWORD`
  environment variable before running other commands.
- For users who prefer manual config, the traditional approach (editing
  `config/mail.local.yaml` by hand) is unaffected and fully supported.

## The migrate-config command

The single-account ("mono") config shape is **removed**. A mono YAML file no
longer loads at runtime — it fails with an actionable error pointing at
`migrate-config` (to convert the existing file) and `detect` (to regenerate it
from scratch). To convert an existing mono file to the multi-account
`accounts:` shape, run:

```sh
robotsix-auto-mail migrate-config
```

This reads the canonical config file (`config/mail.local.yaml` by default, or
`--config PATH`), rewrites it into a one-entry `accounts:` container that
**preserves every value verbatim**, and sets `default_account` to the migrated
account's id. Before overwriting, it writes a backup of the original to
`<path>.bak`.

| Option | Required | Default | Purpose |
|---|---|---|---|
| `--config PATH` | no | the canonical config path | Config file to migrate |
| `--id ID` | no | `default` | Account id assigned to the migrated single account |
| `--dry-run` | no | – | Print the migrated YAML to stdout without writing the file or the backup |

Behaviour:

- A file already in the multi-account shape is left untouched (a clear
  "already migrated" message is printed; exit 0).
- A missing file is an error (exit 1).
- A migrated account that had no explicit `store.path` gets the per-account
  default `.data/<id>/mail.db`; an explicit `store.path` is preserved.

## Configuration keys

### YAML config file (`config/mail.local.yaml`)

Copy `config/mail.local.example.yaml` and fill in your values. Any field you
omit falls back to its built-in default.

```yaml
imap:
  host: imap.example.com
  # port: 993
  # tls_mode: direct-tls
  # folder: INBOX

smtp:
  host: smtp.example.com
  # port: 587
  # tls_mode: starttls

auth:
  username: user@example.com
  password: ""  # set your password here, or via the MAIL_PASSWORD env var
  # OAuth2 / XOAUTH2 — for Gmail, Microsoft 365, or any provider that
  # requires modern SASL XOAUTH2.  When oauth2_token is set, password
  # auth is not used.  See "OAuth2 (XOAUTH2)" section below.
  # oauth2_token: ""
  # oauth2_client_id: ""
  # oauth2_client_secret: ""

# store:
#   path: .data/mail.db

# archive:
#   root: robotsix-mail-archive
#   namespace: ""
#   enabled: true

# llm:
#   api_key: sk-or-v1-…   # or via the LLM_API_KEY env var
#   provider: openrouter-deepseek

# langfuse:
#   public_key: ""
#   secret_key: ""
#   base_url: ""
```

| Key | Required | Default | Purpose |
|---|---|---|---|
| `imap.host` | yes | – | IMAP server hostname |
| `imap.port` | no | `993` | IMAP server port |
| `imap.tls_mode` | no | `"direct-tls"` | IMAP TLS mode |
| `imap.folder` | no | `"INBOX"` | IMAP mailbox folder name |
| `smtp.host` | yes | – | SMTP server hostname |
| `smtp.port` | no | `587` | SMTP server port |
| `smtp.tls_mode` | no | `"starttls"` | SMTP TLS mode |
| `auth.username` | yes | – | Login username (typically the full email address) |
| `auth.password` | no | – | Login password (may instead be supplied via `MAIL_PASSWORD`) |
| `auth.oauth2_token` | no | – | OAuth2 access token for SASL XOAUTH2 (overrides password auth when set) |
| `auth.oauth2_client_id` | no | – | OAuth2 client ID (required by some providers alongside the token) |
| `auth.oauth2_client_secret` | no | – | OAuth2 client secret (required by some providers alongside the token) |
| `auth.oauth2_provider` | no | – | MSAL OAuth2 provider; set to `microsoft` to acquire/refresh tokens via MSAL instead of a password |
| `auth.oauth2_tenant` | no | `"organizations"` | Azure AD tenant for MSAL-managed OAuth2 |
| `store.path` | no | `".data/mail.db"` | Filesystem path for the SQLite database |
| `ingest.interval_minutes` | no | `15` | Minutes between automatic ingest cycles (`ingest --watch`) |
| `archive.root` | no | `"robotsix-mail-archive"` | Root folder for the self-managed archive structure |
| `archive.namespace` | no | `""` | IMAP namespace prefix for archive folders (e.g. `"INBOX."`) |
| `archive.enabled` | no | `true` | Whether to create/manage the archive folder structure |
| `triage.on_ingest` | no | `true` | Whether to run the inbox triage agent automatically after each ingest |
| `calendar.enabled` | no | `true` | Whether the 'Add to Calendar' button appears in the detail view and dispatches to the `robotsix-calendar` agent |
| `calendar.transport` | no | `"in-process"` | Transport mode for calendar dispatch — `in-process` (local `Registry`) or `brokered` (secured broker server) |
| `calendar.broker_host` | no | – | Broker server hostname (required when `transport: brokered`) |
| `calendar.broker_port` | no | `8443` | Broker server port |
| `calendar.broker_tls_ca` | no | – | Path to CA certificate PEM for broker TLS verification |
| `calendar.broker_client_cert` | no | – | Path to client certificate PEM for mutual TLS (optional) |
| `calendar.broker_client_key` | no | – | Path to client key PEM for mutual TLS (optional) |
| `calendar.broker_token` | no | – | Agent authentication token for the broker (redacted in logs/repr) |
| `llm.api_key` | no | – | LLM provider API key for `detect` / mail processing (may instead be supplied via `LLM_API_KEY`) |
| `llm.provider` | no | `"openrouter-deepseek"` | LLM backend name (e.g. `openrouter-deepseek`, `claude-sdk`); see robotsix-llmio README for available backends |
| `langfuse.public_key` | no | – | Langfuse public key; when set with the secret key, every LLM agent run is traced |
| `langfuse.secret_key` | no | – | Langfuse secret key (redacted in logs/repr) |
| `langfuse.base_url` | no | – | Langfuse host URL (falls back to llmio's own default when unset) |
| `board_agent.enabled` | no | `false` | Enable the board agent — an optional agent-comm bridge that exposes the mill board's ticket lifecycle to other agents |
| `board_agent.api_url` | no | – | Board agent API base URL (required when enabled) |
| `board_agent.api_token` | no | – | Board agent API authentication token (redacted in logs/repr) |
| `board_agent.repo_id` | no | – | Board repository identifier (required when enabled) |
| `board_agent.write_ops` | no | `true` | Whether write operations (file, comment, transition, approve, merge, resume, migrate) are allowed via the board agent; set to `false` for a read-only agent |
| `logging.level` | no | `INFO` | Minimum log level — one of `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `logging.format` | no | `console` | Log renderer — `json` for structured logs, `console` for human-friendly dev output |
| `logging.file_dir` | no | `.mail_log` | Directory for date-stamped debug log files; empty disables file logging |

**Trace ID injection.** Every log event automatically includes a `trace_id` field that correlates logs with OpenTelemetry / Langfuse recordings. When a Langfuse trace is active (see `langfuse.public_key` / `langfuse.secret_key` above), the `trace_id` is stamped as a 32-character lowercase hexadecimal string; when no trace is active (or OpenTelemetry is absent), it is set to `"-"`. This is transparent — no configuration is needed — and applies to both `json` and `console` log formats.

### Board agent (agent-comm bridge)

The board agent is an **optional, opt-in** agent-comm service that exposes
the mill board's full ticket lifecycle over agent-comm messages. When
enabled, other agents can drive the board programmatically — querying,
filing, commenting, transitioning, approving, merging, resuming, and
migrating tickets — instead of via the HTTP API or a human.

It is **disabled by default**.  To enable it, set `board_agent.enabled: true`
in your YAML config (or `BOARD_AGENT_ENABLED=true` in the environment), and
provide the required `api_url`, `api_token`, and `repo_id` values.

The `write_ops` gate (default `true`) lets you run the agent in read-only
mode: set `board_agent.write_ops: false` (or `BOARD_AGENT_WRITE_OPS=false`)
and write operations (file, comment, transition, approve, merge, resume,
migrate) are blocked — the agent only services read requests (query).

The board agent requires the `robotsix-board-agent` Python package, which
is declared as a git dependency in `pyproject.toml`.  When the dependency
is not installed and the agent is enabled, a warning is logged to stderr
and the server starts normally without the agent.

> **Multi-account note:** In the multi-account shape (``accounts:`` list),
> ``llm:``, ``langfuse:``, and ``board_agent:`` are **top-level** sections
> (alongside ``default_account:``), not per-account.  See
> ``config/mail.local.example.yaml`` for the correct placement.

The `auth.password` and `llm.api_key` values are **redacted** in logs and
debug output regardless of how they are supplied.

Setting `langfuse.public_key` / `langfuse.secret_key` (or the matching
`LANGFUSE_*` env vars) enables Langfuse tracing for every LLM-running
subcommand. Since `config/mail.local.yaml` is git-ignored, the deployment
supplies the real keys there without committing them.

### Calendar (Add to Calendar)

The detail view of each mail record shows an **Add to Calendar** button.
Clicking it dispatches a `CalendarEventRequest` message to the
`robotsix-calendar` agent over the `robotsix_agent_comm` message bus (a
fire-and-forget send — the button does not wait for a response).

**Enabling / disabling.**  The feature is enabled by default.  Set
`calendar.enabled` to `false` in your YAML config (or
`MAIL_CALENDAR_ENABLED=false` in the environment) to hide the button and
suppress dispatch entirely:

```yaml
# Per-account (under each accounts: entry)
calendar:
  enabled: false
```

```sh
# Environment
MAIL_CALENDAR_ENABLED=false
```

> **Per-account field.** `calendar.enabled` is per-account (like
> `archive.enabled` and `triage.on_ingest`), not a top-level section like
> `board_agent`. Each configured mailbox can independently enable or disable
> the calendar action.

**Dependency.** The calendar feature requires the optional
`robotsix_agent_comm` package. When the dependency is missing and the feature
is enabled, the button still renders but clicking it shows an error alert
from the server's 503 response. When disabled (`calendar.enabled: false`),
the button is not rendered at all — no dependency is needed.

**Message format.** The dispatched `CalendarEventRequest` is a JSON message
with the following fields:

| Field | Type | Purpose |
|---|---|---|
| `correlation_id` | string | Unique identifier for the request/response lifecycle |
| `message_id` | string | The original email's Message-ID |
| `subject` | string | Email subject line |
| `sender` | string | Email sender address |
| `body_text` | string | Plain-text body of the email |
| `email_date` | string | ISO-8601 timestamp of the email |
| `extracted_dates` | string[] | Date/time references extracted from the body via `DATE_TIME_RE` |

### Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `MAIL_IMAP_HOST` | yes | – | IMAP server hostname |
| `MAIL_SMTP_HOST` | yes | – | SMTP server hostname |
| `MAIL_USERNAME` | yes | – | Login username (typically the full email address) |
| `MAIL_PASSWORD` | yes | – | Login password |
| `MAIL_OAUTH2_TOKEN` | no | – | OAuth2 access token for SASL XOAUTH2 (overrides password auth when set) |
| `MAIL_OAUTH2_CLIENT_ID` | no | – | OAuth2 client ID |
| `MAIL_OAUTH2_CLIENT_SECRET` | no | – | OAuth2 client secret |
| `MAIL_OAUTH2_PROVIDER` | no | – | MSAL OAuth2 provider; set to `microsoft` to use MSAL-managed tokens |
| `MAIL_OAUTH2_TENANT` | no | `organizations` | Azure AD tenant for MSAL-managed OAuth2 |
| `MAIL_IMAP_PORT` | no | `993` | IMAP server port |
| `MAIL_IMAP_TLS_MODE` | no | `direct-tls` | TLS negotiation for IMAP — one of `direct-tls`, `starttls`, `none` |
| `MAIL_SMTP_PORT` | no | `587` | SMTP server port |
| `MAIL_SMTP_TLS_MODE` | no | `starttls` | TLS negotiation for SMTP — one of `starttls`, `direct-tls`, `none` |
| `MAIL_IMAP_FOLDER` | no | `INBOX` | IMAP mailbox folder name |
| `MAIL_DB_PATH` | no | `.data/mail.db` | Filesystem path for the SQLite database |
| `MAIL_INGEST_INTERVAL` | no | `15` | Minutes between automatic ingest cycles (`ingest --watch`) |
| `MAIL_ARCHIVE_ROOT` | no | `robotsix-mail-archive` | Root folder for the self-managed archive structure |
| `MAIL_ARCHIVE_NAMESPACE` | no |  | IMAP namespace prefix for archive folders (e.g. `INBOX.`) |
| `MAIL_ARCHIVE_ENABLED` | no | `true` | Whether to create/manage the archive folder structure |
| `MAIL_TRIAGE_ON_INGEST` | no | `true` | Whether to run the inbox triage agent automatically after each ingest |
| `MAIL_CALENDAR_ENABLED` | no | `true` | Whether the 'Add to Calendar' button appears (and dispatch is attempted) |
| `CALENDAR_TRANSPORT` | no | `in-process` | Transport mode for calendar dispatch — `in-process` or `brokered` |
| `CALENDAR_BROKER_HOST` | no | – | Broker server hostname (required when `CALENDAR_TRANSPORT=brokered`) |
| `CALENDAR_BROKER_PORT` | no | `8443` | Broker server port |
| `CALENDAR_BROKER_TLS_CA` | no | – | Path to CA certificate PEM for broker TLS verification |
| `CALENDAR_BROKER_CLIENT_CERT` | no | – | Path to client certificate PEM for mutual TLS (optional) |
| `CALENDAR_BROKER_CLIENT_KEY` | no | – | Path to client key PEM for mutual TLS (optional) |
| `CALENDAR_BROKER_TOKEN` | no | – | Agent authentication token for the broker (redacted in logs/repr) |
| `MAIL_CONFIG_PATH` | no | `config/mail.local.yaml` | Filesystem path to the YAML config file |
| `LLM_API_KEY` | no | – | LLM provider API key (overrides `llm.api_key`); required for `detect` |
| `LLM_PROVIDER` | no | `openrouter-deepseek` | LLM backend name (overrides `llm.provider`); see robotsix-llmio README for available backends |
| `LANGFUSE_PUBLIC_KEY` | no | – | Langfuse public key (overrides `langfuse.public_key`); enables LLM tracing |
| `LANGFUSE_SECRET_KEY` | no | – | Langfuse secret key (overrides `langfuse.secret_key`; redacted) |
| `LANGFUSE_BASE_URL` | no | – | Langfuse host URL (overrides `langfuse.base_url`) |
| `BOARD_AGENT_ENABLED` | no | `false` | Enable the board agent (agent-comm bridge to the mill board) |
| `BOARD_AGENT_API_URL` | no | – | Board agent API base URL |
| `BOARD_AGENT_API_TOKEN` | no | – | Board agent API authentication token (redacted) |
| `BOARD_AGENT_REPO_ID` | no | – | Board repository identifier |
| `BOARD_AGENT_WRITE_OPS` | no | `true` | Allow write operations via the board agent |
| `LOG_LEVEL` | no | `INFO` | Minimum log level — one of `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | no | `console` | Log renderer — `json` for structured logs, `console` for human-friendly dev output |
| `LOG_FILE_DIR` | no | `.mail_log` | Directory for date-stamped debug log files; empty disables file logging |

**TLS modes**

| Mode | Behaviour |
|---|---|
| `direct-tls` | TLS from the first byte, no plaintext negotiation (IMAP port 993, SMTP port 465) |
| `starttls` | Plain connection upgraded to TLS via STARTTLS (IMAP port 143, SMTP port 587) |
| `none` | No TLS at all — **insecure, for local development only** |

### Gmail (app password — simplest)

Gmail supports IMAP, but Google rejects your **normal account password** over
IMAP/SMTP. The simplest working setup needs no OAuth2 client registration — an
**App Password**:

1. Enable IMAP: Gmail → **Settings** → **See all settings** →
   **Forwarding and POP/IMAP** → **Enable IMAP** → **Save Changes**.
2. Turn on **2-Step Verification** for your Google account — App Passwords are
   only offered once 2FA is enabled: <https://myaccount.google.com/security>.
3. Create an App Password at <https://myaccount.google.com/apppasswords>
   (choose "Mail" / "Other"). Google shows a **16-character** value — copy it
   (the spaces are cosmetic and may be omitted).
4. Use that App Password as `auth.password` (or `MAIL_PASSWORD`), with your
   full address as `auth.username`:

   ```yaml
   imap:
     host: imap.gmail.com
     port: 993
     tls_mode: direct-tls
   smtp:
     host: smtp.gmail.com
     port: 587
     tls_mode: starttls
   auth:
     username: you@gmail.com
     # the 16-char App Password, NOT your normal login password
     password: "abcd efgh ijkl mnop"
   ```

   ```sh
   robotsix-auto-mail probe
   ```

If you supply your normal password by mistake, authentication fails with an
`Invalid credentials` error; because the host is Gmail, the client appends a
reminder to use an App Password.

> **Labels, not folders.** Gmail exposes its *labels* as IMAP folders, with the
> system folders under the `[Gmail]/` namespace (`All Mail`, `Sent Mail`,
> `Trash`, …) flagged as special-use. The self-managed archive creates its
> folders as ordinary Gmail **labels** and archives a message by copying it to
> the destination label and removing it from `INBOX` — so the message keeps
> resting in **All Mail** (Gmail's native "archived" state) with the new label
> applied. These special-use system folders are excluded from the LLM
> archive-layout proposal, so it never files mail into `All Mail` or `Trash`.
>
> **Expunge behaviour (important).** Archiving relies on Gmail's *default*
> IMAP setting — Gmail → Settings → Forwarding and POP/IMAP → *"When a message
> is marked as deleted and expunged from the last visible IMAP folder:
> **Archive the message**"*. If you changed this to *"Move the message to the
> Trash"*, archiving from `INBOX` will **trash** mail instead — keep the
> default.

### OAuth2 (XOAUTH2)

Microsoft 365 has deprecated basic auth and now requires **SASL XOAUTH2** — an
industry-standard OAuth2-based SASL mechanism. Gmail also rejects your normal
account password, but accepts either an
[App Password](#gmail-app-password--simplest) (simplest — see above) or
XOAUTH2.

When ``oauth2_token`` is set (in the YAML config or via ``MAIL_OAUTH2_TOKEN``),
the IMAP and SMTP clients authenticate via XOAUTH2 instead of the legacy
``login()`` call.  Password auth is only used when no token is present.

#### Obtaining an OAuth2 token

**Gmail / Google Workspace:**

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and
   create a project (or use an existing one).
2. Enable the **Gmail API** (not Gmail itself — the API is what OAuth2 scopes
   connect to) under "APIs & Services" → "Library".
3. Under "APIs & Services" → "Credentials", create an **OAuth 2.0 Client ID**
   with application type "Desktop app".  Note the **Client ID** and
   **Client Secret**.
4. Use Google's OAuth2 playground or a tool like
   [`gmail-oauth2-tools`](https://github.com/google/gmail-oauth2-tools) to
   obtain an access token.  The required scope for IMAP is
   ``https://mail.google.com/`` (this covers both IMAP and SMTP).

   A minimal offline flow with ``gmail-oauth2-tools``:

   ```sh
   python oauth2.py --generate_oauth2_token \
       --client_id=<CLIENT_ID> \
       --client_secret=<CLIENT_SECRET>
   ```

5. Set the resulting access token as ``auth.oauth2_token`` (or
   ``MAIL_OAUTH2_TOKEN``).  If your flow requires it, also set
   ``auth.oauth2_client_id`` and ``auth.oauth2_client_secret``.

**Microsoft 365 / Outlook.com (MSAL device-code, recommended):**

Microsoft 365 rejects password auth and requires XOAUTH2. Rather than pasting
a short-lived access token into ``auth.oauth2_token``, set
``auth.oauth2_provider: microsoft`` and let the bundled MSAL integration
acquire and silently refresh tokens for you:

1. Install the optional dependency: ``pip install 'robotsix-auto-mail[microsoft]'``.
2. Run [`robotsix-auto-mail detect <address>`](#scripting-usage). When the
   detected host is Microsoft (``outlook.office365.com`` /
   ``*.office365.com`` / ``outlook.com``), `detect` writes an
   ``oauth2_provider: microsoft`` auth block (**no password**) and
   automatically runs the device-code login: it prints a URL and a short code;
   open the URL, enter the code, and sign in to consent. The post-write
   verification then authenticates over XOAUTH2 on both IMAP and SMTP.
3. To (re)run the consent flow later — e.g. after revoking access or moving to
   a new machine — run
   ``robotsix-auto-mail auth login --account <id>``.

The MSAL refresh-token cache is stored per account at
``.data/<id>/msal_cache.json``; once seeded, ``ingest --watch`` refreshes
access tokens silently for hours without re-prompting. The cache file holds
secrets and is never committed to the repo.

By default the integration uses a well-known public client id suitable for
IMAP/SMTP device-code flow against the ``organizations`` tenant. Organisations
with their own Azure AD **app registration** can override these via
``auth.oauth2_client_id`` (and ``auth.oauth2_tenant`` for a single-tenant
directory id); the scopes used are
``https://outlook.office365.com/IMAP.AccessAsUser.All``,
``https://outlook.office365.com/SMTP.Send`` and ``offline_access``.

> **Admin-consent caveat (corporate tenants).** Many Microsoft 365
> organisations restrict which applications may use IMAP/SMTP OAuth. If
> device-code login fails with a consent/permission error, an **Azure AD
> administrator** may need to grant the ``IMAP.AccessAsUser.All`` and
> ``SMTP.Send`` delegated permissions (and admin-consent) for the client id
> before the flow succeeds.

**Microsoft 365 / Outlook.com (static token, manual):**

If you prefer to manage tokens yourself, register an application in the
[Azure Portal](https://portal.azure.com/) under "App registrations", add the
``IMAP.AccessAsUser.All`` and ``SMTP.Send`` delegated permissions, run the
OAuth2 device-code or authorization-code flow with scopes
``https://outlook.office.com/IMAP.AccessAsUser.All`` and
``https://outlook.office.com/SMTP.Send``, and set the resulting access token
as ``auth.oauth2_token`` (you are then responsible for refreshing it).

**Self-hosted / other providers:**

If your IMAP/SMTP server still accepts password-based ``login()``, leave the
OAuth2 fields unset — the existing password path works as before.

### Multiple accounts

`robotsix-auto-mail` can manage more than one mailbox at once. Multiple
accounts are modelled as N independent configurations — each account is a
complete set of the connection settings described above, plus a stable
`id` and an optional human-friendly `label`. The `accounts:` list is the only
supported runtime config-file shape; the single-account `MAIL_*` environment
described above is retained for isolated boots, but a single-account ("mono")
YAML **file** is no longer loaded (see below).

**One SQLite DB per account.** Rather than tagging every database row with an
`account_id`, each account carries its **own** `store.path` (SQLite database
file). Per-account state (triage decisions, sender memory, archive
watermarks) is therefore naturally isolated with zero schema changes, and the
existing per-config `store.path` is the only plumbing needed. The cost is one
SQLite file per account, so every account's `store.path` must be unique —
uniqueness is enforced when the configuration loads. When an account omits
`store.path`, it defaults to a dedicated per-account folder `.data/<id>/mail.db`,
which is unique per account and created automatically on first DB use.

> The single-account ("mono") YAML **file** shape — top-level `imap:` /
> `smtp:` / `auth:` sections with no `accounts:` key — is **no longer
> supported**: such a file fails to load with an actionable error. Run
> [`robotsix-auto-mail migrate-config`](#the-migrate-config-command) to convert
> an old config to the multi-account shape, or
> [`robotsix-auto-mail detect`](#scripting-usage) to regenerate it. (The
> single-account `MAIL_*` **environment** is unaffected and remains supported.)

**YAML shape.** A multi-account YAML file uses a top-level `accounts:` list
instead of the single-account top-level sections. Each list entry is a
mapping with a required string `id`, an optional `label`, and the usual
nested `imap` / `smtp` / `auth` / `store` (and optional `llm` / `ingest` /
`archive` / `triage`) sections — parsed exactly as in the single-account
file. An optional top-level `default_account:` names the default account;
when omitted, the first entry is the default. The canonical example ships in
`config/mail.local.example.yaml`:

```yaml
default_account: personal

accounts:
  - id: personal
    label: Personal Gmail
    imap:
      host: imap.gmail.com
    smtp:
      host: smtp.gmail.com
    auth:
      username: me@gmail.com
    store:
      path: .data/personal/mail.db

  - id: work
    label: Work mailbox
    imap:
      host: imap.work.example.com
    smtp:
      host: smtp.work.example.com
    auth:
      username: me@work.example.com
    store:
      path: .data/work/mail.db

  - id: office365
    label: Microsoft 365
    imap:
      host: outlook.office365.com
    smtp:
      host: smtp.office365.com
    auth:
      username: me@contoso.com
      oauth2_provider: microsoft
      oauth2_tenant: organizations
    store:
      path: .data/office365/mail.db
```

The Microsoft 365 account above carries **no password** — run
`robotsix-auto-mail auth login --account office365` (or let `detect` do it)
to seed the MSAL token cache at `.data/office365/msal_cache.json`.

**Environment-variable scheme.** Each per-field environment variable is
namespaced per account by inserting `ACCOUNTS_<n>_` after `MAIL_`, where `<n>`
is a zero-based account index. A field whose single-account variable is
`MAIL_<X>` becomes `MAIL_ACCOUNTS_<n>_<X>` (for example
`MAIL_ACCOUNTS_0_IMAP_HOST`, `MAIL_ACCOUNTS_1_PASSWORD`); `LLM_API_KEY`, `LLM_PROVIDER`, and `LANGFUSE_*` are application-wide (global) and read from the bare env vars, not namespaced. Two
extra namespaced variables describe the account itself: `MAIL_ACCOUNTS_<n>_ID`
(required — the stable account id, e.g. `MAIL_ACCOUNTS_0_ID=personal`) and
`MAIL_ACCOUNTS_<n>_LABEL` (optional). Account indices must be contiguous
starting at 0 (a gap raises an error). An optional `MAIL_ACCOUNTS_DEFAULT`
names the default account id. As with `store.path` in YAML, an account whose
`MAIL_ACCOUNTS_<n>_DB_PATH` is unset defaults to `.data/<id>/mail.db`.

### Self-managed archive structure

`robotsix-auto-mail` manages its own archive folder hierarchy, rooted at
`archive.root` (default `robotsix-mail-archive`). On the first ingest a quick
LLM call proposes an appropriate layout based on the mailbox's existing
folders; the resulting folder list is then persisted in the SQLite
`watermark` table under the key `archive_structure` and reused verbatim on
every subsequent run — no folders are listed, no LLM is called, and nothing
is recreated.

Set `archive.enabled` (env `MAIL_ARCHIVE_ENABLED`) to `false` to disable
archive management entirely: `setup_archive` is never called, no watermark is
written, and ingestion proceeds normally. Re-enabling it later runs setup on
the next ingest (since the watermark was never set).

Because the structure is remembered after the first run, **changing
`archive.root` afterwards does not move or recreate any folders** — the
persisted `archive_structure` watermark short-circuits subsequent runs. A new
root only takes effect on a fresh run that has no watermark yet.

## Precedence rules

`mail.load()` resolves configuration in this order:

1. **Environment variables are evaluated first.** If all four required
   variables (`MAIL_IMAP_HOST`, `MAIL_SMTP_HOST`, `MAIL_USERNAME`,
   `MAIL_PASSWORD`) are set, they are used and the file is ignored.
2. **File fallback.** If only required fields are missing from the
   environment (no invalid values), `load()` reads the YAML config file at
   `MAIL_CONFIG_PATH` (default: `config/mail.local.yaml`).
3. **Env-override merge.** Every environment variable that *is* set is
   then re-applied on top of the file values. This lets you keep shared
   settings in the config file while overriding just the password via
   `MAIL_PASSWORD`, for example.

Fields absent from both the file and the environment fall back to their
built-in defaults.

If any environment variable has an *invalid* value (e.g. a non-integer
port), the error is raised immediately — the file fallback is skipped so
your typo is not silently swallowed.

**LLM settings** (`llm.api_key`) follow the same rule —
`LLM_API_KEY` overrides the file's `llm:` section. The `detect`
command resolves them on their own (via `load_llm()`) so it works before the
mail fields are filled in.

## Example setups

### Docker Compose with YAML (recommended)

```yaml
# config/mail.local.yaml (git-ignored)
imap:
  host: imap.mail.example.com
  port: 993
  tls_mode: direct-tls

smtp:
  host: smtp.mail.example.com
  port: 587
  tls_mode: starttls

auth:
  username: user@mail.example.com
  password: your-app-password-here
```

```sh
docker compose run robotsix-auto-mail probe
```

### Generic IMAP + SMTP (.env)

```sh
# .env
MAIL_IMAP_HOST=imap.mail.example.com
MAIL_IMAP_PORT=993
MAIL_IMAP_TLS_MODE=direct-tls
MAIL_SMTP_HOST=smtp.mail.example.com
MAIL_SMTP_PORT=587
MAIL_SMTP_TLS_MODE=starttls
MAIL_USERNAME=user@mail.example.com
MAIL_PASSWORD=your-app-password-here
```

### Config file + password from the environment

Keep non-secret settings in the YAML file and supply only the password via
`MAIL_PASSWORD` (which overrides `auth.password`):

```yaml
# config/mail.local.yaml (git-ignored)
imap:
  host: imap.mail.example.com
smtp:
  host: smtp.mail.example.com
auth:
  username: user@mail.example.com
  # password omitted — supplied via MAIL_PASSWORD below
```

```sh
export MAIL_PASSWORD=your-app-password-here
robotsix-auto-mail probe
```

## The `probe` command

Once your configuration is in place, run the probe to verify connectivity:

```sh
$ robotsix-auto-mail probe
```

### What it does

`probe` loads the mail configuration, then:

- Opens an authenticated IMAP connection and prints the server greeting,
  capability list, and mailbox folder listing.
- Opens an authenticated SMTP connection and prints the EHLO response
  and ESMTP feature set.

No email is read or sent — this is a read-only diagnostic command.

### Representative output

```text

IMAP Probe
------------------------------------------------------------
Greeting: * OK [CAPABILITY IMAP4rev1 …] IMAP server ready
Capabilities:
  - IMAP4rev1
  - STARTTLS
  - AUTH=PLAIN
  - …

Folders:
  INBOX
    attributes: (none)
    delimiter:  /
  Drafts
    attributes: \HasNoChildren
    delimiter:  /
  Sent
    attributes: \HasNoChildren
    delimiter:  /

SMTP Probe
------------------------------------------------------------
EHLO response: 250-smtp.mail.example.com
250-PIPELINING
250-SIZE 35651584
250-STARTTLS
250-AUTH PLAIN LOGIN
250-ENHANCEDSTATUSCODES
250 8BITMIME

ESMTP features:
  AUTH: PLAIN LOGIN
  ENHANCEDSTATUSCODES: (empty)
  PIPELINING: (empty)
  SIZE: 35651584
  STARTTLS: (empty)
```

Exit code is `0` when both probes succeed, `1` when either fails.

## The `ingest` command

The ingestion pipeline is documented separately — see
[docs/ingestion.md](ingestion.md) for the full ingestion model, datastore
schema, idempotency guarantees, configuration, and CLI usage.

## The `board` command

Once mail has been ingested (see [The `ingest` command](#the-ingest-command)),
view it with the read-only board:

```sh
$ robotsix-auto-mail board
```

`board` opens the local SQLite datastore and prints an "Inbox" header followed
by a rendered card for each stored message.  Each card shows:

- `From:` — the sender's address
- `Subject:` — the message subject (or `(no subject)` when blank)
- `Date:` — formatted as `YYYY-MM-DD HH:MM` (UTC)
- a body preview — the first 150 characters of the plain-text body, truncated
  with `…` when longer (or `(no body)` when no plain-text body is available)

Cards are separated by a 60-character `-` rule.  A message count line follows
the last card.

When the inbox is empty the command prints `Your inbox is empty.`.

The command is read-only — it never modifies the database or contacts a mail
server.

### Representative output

```text

Inbox
------------------------------------------------------------
From:    alice@example.com
Subject: Hello
Date:    2025-06-01 14:30

Just checking in!
------------------------------------------------------------
From:    bob@example.com
Subject: Meeting notes
Date:    2025-06-02 09:15

Here are the notes from this morning's standup.  We agreed to
move the deadline to Friday and Alice will follow up on the…
2 message(s)
```

### Empty inbox

```text

Inbox
------------------------------------------------------------
Your inbox is empty.
```

Exit code is `0` on success, `1` when configuration cannot be loaded.

## The `serve` command

For a persistent, browser-based view of ingested mail, use the `serve`
subcommand.  This starts a long-running HTTP server that hosts a read-only
kanban board at `/board`.

```sh
$ robotsix-auto-mail serve
# Listening on http://0.0.0.0:8080/board
```

### Options

| Option | Default | Purpose |
|---|---|---|
| `--account` | – | Account id to serve; when omitted, the container's default account is served. This account is used as the default for requests that omit `?account=`. |
| `--port` | `8080` | Port to listen on |

### The board page

Open `http://localhost:<port>/board` in a browser.  The page shows ingested
mail in **five columns** — Needs reply, Waiting on them, To read, No action, Done — each with a card
count badge.  Every mail card has a **Move** dropdown that lets you change
the card's status column via `POST /move`.

**Account picker (multi-account mode).**  When two or more accounts are configured
(via `config/mail.accounts.yaml` or environment variables), an account picker
dropdown appears in the page header. The dropdown
shows each configured account with its `label` (or `id` if no label is set), and
you can click to switch accounts. Switching navigates to `/board?account=<id>` and
sets an `account` cookie (via `Set-Cookie: account=<id>; Path=/`) so every
subsequent request on the page routes to the chosen account. The selected account
is also threaded into the detail iframe and content refresh requests so deep-links
and cookie-less clients maintain account context. When only one account is
configured, the picker does not appear and the board behaves as a single-account
view.

**Triage badges.**  When a mail record has a triage decision, the card displays a
small **triage badge** showing the action label (one of `answer`, `waiting`,
`archive`, `delete`, `ignore`, or `user_triage`) with the decision reason visible in a
tooltip when you hover over the badge. Cards without a triage decision show no
badge.

**Detail drawer.**  Click any card to open a detail drawer showing the full
message, including the **Triage** field with the decision action, reason, source
(agent or user), and confidence level. If no decision has been recorded, the
Triage field shows "(no triage decision)".

**Draft generation.**  For messages marked "Needs reply" (TO_ANSWER triage action),
the detail drawer shows a **Draft reply** section with two interfaces:
- A **Generate with AI** button (when no draft exists) or **Regenerate with AI**
  button (when a draft already exists) that uses an LLM to prepare a concise,
  professional reply draft in the same language as the incoming message. The LLM
  incorporates any notes or instructions you have written in the **Notes** field
  — use them to guide the draft (e.g., "decline politely", "mention invoice paid",
  "propose Tuesday afternoon"). The generated draft appears in a textarea below
  the button, ready for review and manual editing.
- A **Save draft** form to persist your (edited) draft text and move the card to
  "Draft ready" status.

On the board card itself, a **Draft reply** button (visible only for TO_ANSWER
cards) is a shortcut — click it to immediately generate a draft and re-open the
detail drawer to show the result.

**Sending replies.**  Once a draft is saved (the card moves to "Draft ready" status),
two additional buttons appear in the detail drawer:
- **Reply** — sends the draft text as a reply to the original sender via SMTP,
  then re-queues the original message for triage with the sent reply body stored.
  The card reappears in the INBOX column and the triage agent will decide the
  final disposition (typically to archive, but the agent may decide otherwise based
  on the reply content). This allows the triage system to own the post-answer workflow.
- **Reply to all** — sends the draft to the original sender and includes all
  recipients from the original message (the `To` and `Cc` lists, excluding your own address
  and duplicates). After sending, the message is re-queued for triage in the same manner.

The reply always includes threading headers (`In-Reply-To` and `References`) so it appears
as a conversation thread in the recipient's mail client. The subject is automatically prefixed
with "Re: " unless it already starts with that (case-insensitive).

**Archive-folder recommendations.**  When you move a card to the "To archive" column
(or the triage agent classifies one), the system proposes an archive subfolder. The
proposal engine uses a **three-tier strategy**:

1. **User override** — if you have manually set a subfolder for that message, it is used.
2. **LLM-learned history** — the system remembers which archive subfolders you (or the triage
   agent) have filed mail from each sender and domain into. When proposing a folder for a
   similar sender (same email address, or same domain with a known project folder), it
   suggests reusing the existing project folder instead of creating a new one. This is
   especially useful when a domain hosts multiple project addresses — the system learns
   that `armada@ls2n.fr` and `crew@ls2n-fr.org` both relate to the `ls2n/armada` project
   and steers both there.
3. **Deterministic fallback** — if no history exists, the system proposes a folder based
   on a simple rule: mailing-list brackets in the subject (`[python-dev]` → `Lists/python-dev`),
   sender domain + local part (`alice@example.com` → `example.com/alice`), or a year/month
   bucket from the message date.

The board is the interface: no separate client is needed.

The page includes `<meta http-equiv="refresh" content="30">`, so the board
auto-refreshes every 30 seconds.

**Column-wide batch delete and archive.**  For columns with many cards,
individual card operations (delete one, archive one) can be tedious. The board
offers two column-wide bulk operations, each accessed via an **All** button that
appears only when the operation is not already running:

- **Delete All** (appears on the `TO_DELETE` column): bulk-deletes every message
  in the column from both IMAP and the local database via `POST /batch-delete`.
  Click the button and confirm the dialog to start; the operation runs in a
  background daemon thread and does not block the board. While running, a
  progress banner appears showing the operation status (e.g., "Deleting mail:
  120/518. The board will refresh automatically.") and the Delete All button is
  suppressed until the operation completes.

- **Archive All** (appears on the `TO_ARCHIVE` column): bulk-archives every
  message in the column to its proposed subfolder via IMAP (or removes it from
  the local database if IMAP is not configured) via `POST /batch-archive`. Click
  the button and confirm the dialog to start; like delete, the operation runs in
  the background with a progress banner. The archive operation groups messages by
  their destination subfolder so each group is moved in a single batched IMAP
  operation, minimizing round-trips (a 518-mail column with multiple destination
  folders costs at most ~6 IMAP round-trip pairs instead of one per message).

- **Per-destination groups** (`TO_ARCHIVE` column): the column's cards are
  ordered by destination folder and split into labelled groups, each headed by
  the destination (and card count) and an **Archive these N →** button. The
  button archives only that group's mail to its destination via
  `POST /batch-archive-folder` (form field `folder` = the destination subfolder
  relative to the archive root; empty = the root). This lets you review and
  approve one destination at a time instead of the whole column. It shares the
  same single-flight guard and background worker as **Archive All**.

**Progress and single-flight guard.**  Only one batch operation (delete or
archive) can run at a time per account — a second request to start an operation
while one is already in flight returns a 302 redirect to `/board` with no action.
The board's 30-second auto-refresh polls the operation status and hides the
progress banner once complete. If a batch operation is interrupted by a container
restart (SIGKILL), the watermark is reset at startup so the board recovers
cleanly without a wedged banner.

**All-mailboxes (aggregate) view.**  In multi-account mode the **All mailboxes**
selection shows a unified board merging every account's cards. The **Delete All**
button is available here too: it posts to `/batch-delete?account=__all__`, which
fans the operation out to every account that has `TO_DELETE` mail — starting one
independent background worker per account (each against its own database and IMAP
connection). Accounts already running a batch op, or with nothing to delete, are
skipped. The progress banner sums the per-account workers' progress, and the
button is suppressed while any of them is still in flight. The **Archive All**,
per-destination archive, and **Force Triage** controls remain per-account and do
not appear in the aggregate view (switch to a single account to use them).

**Re-triggering after interruption.**  Because each batch processes records in
chunks and commits to the database per chunk, a mid-operation restart leaves
already-deleted/archived records removed from the database. Re-triggering the same
batch operation automatically skips the already-processed records and continues
with the remaining ones — so a 518-mail delete that was interrupted at 300 mails
will, on re-trigger, process only the remaining 218 without re-deleting the first
300.

### Multi-account request routing

When multiple accounts are configured (via `config/mail.accounts.yaml` or
environment variables), the `serve` command hosts all accounts at a single
HTTP server address. Per-request account selection determines which account's
database and mail config are used to handle each request.

**Account selection precedence** (checked in this order):

1. **Explicit query parameter** — `?account=<id>` (e.g. `/board?account=work`)
2. **Cookie** — an `account` cookie set by a prior successful query param selection
3. **Default account** — either the account passed via `serve --account <id>`, or the container's `default_account` from the config

When the HTTP response succeeds with an explicit `?account=<id>`, a `Set-Cookie: account=<id>; Path=/` header is sent so the selection persists across the board's cookie-less JavaScript fetches and POST→redirect flows. This allows the browser to stay on the chosen account without explicit URL parameters on every request.

**Error handling:**

- An explicit `?account=<unknown-id>` returns a 404 (hard failure).
- A stale or unknown id supplied only via cookie is silently ignored — the default account is served instead (cookies must never hard-fail a request).

**Single-account behavior:** When only one account is configured, the
precedence is satisfied immediately (the single account is always the
default); multi-account selection is invisible to the user.

**Example multi-account setup:**

```sh
# Config with two accounts
cat config/mail.accounts.yaml
# default_account: personal
# accounts:
#   - id: personal
#   - id: work

# Start the server (personal is the default)
robotsix-auto-mail serve
# Listening on http://0.0.0.0:8080/board

# Users can select accounts:
# - http://localhost:8080/board (uses personal account)
# - http://localhost:8080/board?account=work (switches to work account, sets cookie)
# - Subsequent requests without ?account= will use the work cookie until cleared

# Or pick a different default at startup:
robotsix-auto-mail serve --account work
# Now requests without ?account= default to work
```

### Contrast with `board`

| | `board` | `serve` |
|---|---|---|
| **Output** | Plain text to stdout | HTML page in a browser |
| **Layout** | Single "To read" column | Five columns (Needs reply, Waiting on them, To read, No action, Done) |
| **Lifetime** | One-shot — prints and exits | Persistent HTTP daemon |
| **Interaction** | Read-only | Move dropdowns (`POST /move`) |
| **Refresh** | Manual (re-run the command) | Automatic (30-second meta refresh) |

Both commands read from the same local SQLite datastore — no configuration
changes are needed to switch between them.

### The `/config-sync` endpoint

In addition to the board page, the server hosts a `POST /config-sync` endpoint
that runs the optional LLM drift advisory agent and returns structured JSON.
This is useful for external schedulers (cron, systemd timer) or monitoring
systems that want to check for configuration drift on demand.

#### Request

```sh
curl -X POST http://localhost:8080/config-sync
```

No request body is required.

#### Response on success (HTTP 200)

Content-Type: `application/json`

```json
{
  "proposals": [
    {
      "title": "imap_folder default mismatch",
      "body": "Docs say INBOX.All but the dataclass default is INBOX.",
      "affected_field": "imap_folder",
      "confidence": "high"
    }
  ]
}
```

When no drift is detected, the `proposals` array is empty.

#### Error responses (HTTP 503)

If the LLM extra (`pydantic-ai`) is not installed or the agent encounters an
error (e.g., missing API key):

```json
{"error": "Config-sync advisory requires the optional LLM extra, which is not installed"}
```

#### Requirements

The endpoint requires the same setup as the CLI `config-sync` command:
- The `pydantic-ai` package (install via `pip install robotsix-auto-mail[dev]`)
- An LLM API key (via `LLM_API_KEY` env or `llm.api_key` in config)

The endpoint applies dedup filtering by default (consulting the persisted
ledger in the SQLite `watermark` table), so previously-seen drift proposals
are suppressed automatically.

### The `/batch-delete` and `/batch-archive` endpoints

In addition to per-card delete and archive operations, the server hosts two
endpoints for column-wide bulk deletions and archives. These endpoints are used
by the board page's **Delete All** and **Archive All** buttons but can also be
called directly by external tools or scripts.

#### `POST /batch-delete` — bulk delete all TO_DELETE mail

Deletes every message in the `TO_DELETE` triage column from both IMAP and the
local database in a background daemon thread.

##### Request

```sh
curl -X POST http://localhost:8080/batch-delete
```

No request body is required.

##### Behavior

When the request succeeds:

1. The server checks the `batch_op:state` watermark. If it is already running
   (indicating a batch operation is in flight), the request returns a 302 redirect
   to `/board` immediately with no action (single-flight guard).
2. If no batch operation is running, the watermark is set to `"running"` and a
   daemon background thread is spawned that:
   - Collects every `TO_DELETE` triage decision and its corresponding `MailRecord`.
   - Processes records in chunks (up to 100 UIDs per chunk to minimize IMAP
     round-trips).
   - For each chunk: issues a single batched `UID STORE +FLAGS (\Deleted)` and
     `EXPUNGE` to mark all UIDs in the chunk deleted, then deletes the
     corresponding local database rows and commits.
   - Updates the `batch_op:state` watermark with progress JSON
     (`{"op": "delete", "done": N, "total": M}`) after each chunk so the board
     can display live progress.
   - Records with `imap_uid is None` (DB-only records) are deleted without IMAP
     operations.
   - Always clears the `batch_op:state` watermark back to `"idle"` in a
     `finally` block, even on error.
3. The server immediately returns a 302 redirect to `/board` so the browser
   returns to the board page. The deletion runs in the background and the board
   auto-refreshes every 30 seconds to reflect the deletion progress and status
   change.

##### Response on success (HTTP 302)

A 302 redirect to `/board` (the batch delete has been queued).

##### Error responses

- **HTTP 302** — A batch operation (delete or archive) is already running (the
  watermark `batch_op:state` is not `None` and not `"idle"`). The response is a
  302 redirect to `/board` (idempotent — the request is silently ignored and the
  watermark is unchanged).

Errors during the background thread (e.g., `ImapError`, database errors) are
swallowed so a transient IMAP failure never wedges the board. The thread always
clears the watermark so the board eventually recovers. Already-deleted records
remain deleted (per-chunk commits make progress durable), so re-triggering the
batch continues with the remaining records.

An optional `?account=<id>` query parameter is supported in multi-account mode
(see [Multi-account request routing](#multi-account-request-routing)).

#### `POST /batch-archive` — bulk archive all TO_ARCHIVE mail

Archives every message in the `TO_ARCHIVE` triage column to its proposed
subfolder via IMAP (or removes it from the local database if IMAP is not
configured) in a background daemon thread.

##### Request

```sh
curl -X POST http://localhost:8080/batch-archive
```

No request body is required.

##### Behavior

When the request succeeds:

1. The server checks the `batch_op:state` watermark. If it is already running, the
   request returns a 302 redirect to `/board` immediately with no action (the same
   single-flight guard as `/batch-delete`, so delete and archive cannot run
   concurrently).
2. If no batch operation is running, the watermark is set to `"running"` and a
   daemon background thread is spawned that:
   - Collects every `TO_ARCHIVE` triage decision and its corresponding `MailRecord`.
   - Computes each record's effective destination subfolder using the same logic
     the board uses for archive-folder recommendations (user override → LLM-learned
     history → deterministic fallback).
   - Groups records by their destination folder so each group can be moved with a
     single batched `UID COPY` command (minimizing IMAP round-trips).
   - For each destination group: ensures the folder hierarchy exists, issues a
     single batched `UID COPY` to the destination, then deletes the corresponding
     local database rows and commits.
   - Records with `imap_uid is None` (DB-only records) are deleted without IMAP
     operations.
   - Updates the `batch_op:state` watermark with progress JSON
     (`{"op": "archive", "done": N, "total": M}`) after each group.
   - Always clears the `batch_op:state` watermark back to `"idle"` in a
     `finally` block, even on error.
3. The server immediately returns a 302 redirect to `/board` so the browser
   returns to the board page. The archival runs in the background and the board
   auto-refreshes every 30 seconds to reflect progress and status changes.

##### Response on success (HTTP 302)

A 302 redirect to `/board` (the batch archive has been queued).

##### Error responses

- **HTTP 302** — A batch operation (delete or archive) is already running (the
  watermark `batch_op:state` is not `None` and not `"idle"`). The response is a
  302 redirect to `/board` (idempotent — the request is silently ignored).

Errors during the background thread (e.g., `ImapError`, invalid destination
folders, database errors) are swallowed. Already-archived records remain archived
(per-group commits make progress durable), so re-triggering the batch continues
with the remaining records.

An optional `?account=<id>` query parameter is supported in multi-account mode
(see [Multi-account request routing](#multi-account-request-routing)).

#### `POST /batch-archive-folder` — archive one destination group

Archives only the `TO_ARCHIVE` mail whose proposed destination equals the
posted folder, leaving the rest of the column untouched. Backs the board's
per-destination **Archive these N →** buttons.

##### Request

```sh
curl -X POST http://localhost:8080/batch-archive-folder \
  -d 'folder=Billing'
```

Form-encoded body parameter:
- `folder` (required): the destination subfolder **relative to the archive
  root** (e.g. `Billing`, `Finance/Banking`). An empty value targets the
  archive root itself.

##### Behavior

Identical to `POST /batch-archive` — same `batch_op:state` single-flight guard,
synchronous stale-UID precheck, and background worker — except the worker keeps
only the records whose effective subfolder (per the archive-folder
recommendation logic) equals `folder`. Returns a 302 redirect to `/board`.
An optional `?account=<id>` query parameter is supported in multi-account mode.

## The `config-sync` command

For operators who want to audit their configuration, the `config-sync`
subcommand runs an **optional, advisory LLM agent** that examines four
configuration surfaces and proposes human-readable drift corrections:

```sh
$ robotsix-auto-mail config-sync
```

This is an **advisory tool only** — it does not replace the deterministic
`scripts/config/check_config_sync.py` CI gate (which is fast and free).
A successful run exits code `0` even when drift is found, so it won't break
operator scripts.

### Advisory tool vs. the deterministic gate

`robotsix-auto-mail` checks configuration consistency at two distinct layers,
and they are **complementary** — neither replaces the other:

| | `scripts/config/check_config_sync.py` | `config-sync` advisory agent |
|---|---|---|
| **Role** | Authoritative CI / pre-commit gate | Optional operator-facing advisory tool |
| **Mechanism** | Deterministic, rule-based checks | LLM inspection of config surfaces |
| **Cost** | Fast and free (no LLM, no API key) | Requires `pydantic-ai` + an LLM API key |
| **Coverage** | Known, encoded drift patterns | *Unanticipated* drift the rules don't encode |
| **On drift** | Fails the build (blocks merge) | Reports proposals; still exits `0` |
| **When it runs** | Every commit / PR, automatically | On demand, when an operator chooses |

`scripts/config/check_config_sync.py` is the **source of truth**: it is the
fast, free, deterministic gate that blocks merges on known configuration drift,
and it runs automatically on every commit and pull request. The `config-sync`
LLM agent (CLI subcommand and `POST /config-sync` endpoint) is an **optional,
operator-facing advisory tool** that surfaces *unanticipated* drift patterns the
deterministic checker doesn't encode. Because a successful advisory run exits
`0` even when it reports drift, it **does not gate anything** — running
`config-sync` is never a substitute for the deterministic gate passing.

### When to use which

- **Rely on the deterministic gate for every commit and PR.** It is automatic,
  free, and authoritative — it is what actually keeps configuration surfaces in
  sync, and a green build means the known drift checks pass.
- **Reach for the advisory tool occasionally / on demand.** Good moments are
  after a large config refactor, when onboarding a new configuration surface, or
  on a periodic external schedule (e.g. a cron job hitting `POST /config-sync`)
  to catch drift the deterministic rules don't yet cover. Treat its proposals as
  hints to review, not as merge blockers.

### Options

| Option | Default | Purpose |
|---|---|---|
| `--api-key` | – | OpenRouter API key; overrides `LLM_API_KEY` env and config file |
| `--output-format` | `text` | Output format: `text` (human-readable) or `json` (machine-readable) |
| `--dedup` | – | Consult/update the dedup memory ledger to suppress previously-seen findings; requires a loadable config (for db path) |

### Requirements

The `config-sync` command requires:
- The `pydantic-ai` package (install via `pip install robotsix-auto-mail[dev]`)
- An LLM API key (via `--api-key`, `LLM_API_KEY` env, or `llm.api_key` in config)

When `--dedup` is **not** passed, the command does not require a full mail config
— it skips config loading and uses `conn=None`. When `--dedup` **is** passed,
it loads the config to retrieve `db_path` for the dedup ledger.

### The dedup memory ledger

Operators who run the advisory tool regularly would otherwise see the same drift
proposals on every run. The **dedup memory ledger** prevents that repeated noise:
it persists a fingerprint of every drift proposal that has already been surfaced,
stored in the SQLite `watermark` table under the key `config_sync_ledger`. On
subsequent runs, proposals whose fingerprints are already recorded (i.e. those
already seen, accepted, or rejected) are suppressed, so only genuinely new drift
is reported.

The two entry points apply the ledger differently, and this asymmetry is
intentional:

- The **CLI** applies dedup only when you pass `--dedup` (which requires a
  loadable config, since the ledger lives in the configured database).
- The **`POST /config-sync` endpoint** applies dedup **by default**, consulting
  and updating the ledger on every request — well suited to a periodic external
  scheduler that should only be alerted about previously-unseen drift.

#### Ledger state semantics

The ledger lives in the SQLite `watermark` table under the key
`config_sync_ledger`, stored as a single JSON object keyed by a per-finding
fingerprint:

```json
{
  "<fingerprint>": {
    "title": "imap_folder default mismatch",
    "affected_field": "imap_folder",
    "state": "pending"
  }
}
```

- **Fingerprint basis.** Each `<fingerprint>` is a SHA-256 hash derived from a
  proposal's **stable identity fields only** — `affected_field` + `title`. The
  `body` is deliberately **excluded** so that a reworded body (the LLM rephrases
  its prose between runs) does not escape dedup and resurface the same finding
  as new.
- **States.** An entry's `state` is one of `pending`, `accepted`, or `rejected`.
  All three suppress re-reporting equally — once a fingerprint is recorded in
  *any* state, that proposal is filtered out of future `--dedup` CLI runs and
  `POST /config-sync` responses.
- **First-seen proposals are recorded as `pending`** automatically. Operators
  can set any of the three states (`pending`, `accepted`, or `rejected`)
  manually via the [`config-sync-set`](#the-config-sync-set-command) command.

### Responding to drift proposals

The advisory agent only *reports* — it never edits config or files anything.
Acting on a proposal is the operator's job. For each `DriftProposal` in the
text or JSON output, look at its `title`, `body`, `affected_field`, and
`confidence`, then decide:

- **Real divergence → reconcile the authoritative surfaces.** If the proposal
  describes a genuine inconsistency, fix it by editing the surfaces the
  deterministic checker compares so that
  `python scripts/config/check_config_sync.py` goes green again. Those surfaces
  are:
  - the `MailConfig` dataclass (`src/robotsix_auto_mail/config/__init__.py`),
  - the YAML template (`config/mail.local.example.yaml`),
  - `.env.example`, and
  - the two config tables in this file — "YAML config file" and "Environment
    variables".

  The `FIELD_TO_YAML` / `FIELD_TO_ENV` mappings in
  `scripts/config/check_config_sync.py` are the **source of truth** for which
  YAML key and environment variable each `MailConfig` field corresponds to;
  reconcile every surface to agree with them.
- **Intentional divergence → ignore the proposal.** If the reported difference
  is a deliberate design choice the deterministic rules simply don't model,
  treat the proposal as a false positive and do nothing — no code change is
  needed.

Either way, the dedup ledger suppresses an already-surfaced proposal on the
next `--dedup` CLI run or `POST /config-sync` request **regardless of your
decision**, because any recorded state (`pending` / `accepted` / `rejected`)
suppresses re-reporting.

#### Worked reconciliation example

Suppose an advisory run surfaces this proposal:

```text

Config Drift Advisory
------------------------------------------------------------

imap_folder documented value mismatch
  confidence: high
  affected field: imap_folder

The `MAIL_IMAP_FOLDER` row in the "Environment variables" table documents a
default of `INBOX.All`, but the MailConfig default for imap_folder is INBOX.
```

You confirm it is a **real drift** — the documented default no longer matches
the dataclass. Reconcile the affected surface(s), e.g. fix the `MAIL_IMAP_FOLDER`
row in the "Environment variables" table (and any other surface that disagrees,
such as `.env.example`) so the documented default reads `INBOX` again:

```text
| `MAIL_IMAP_FOLDER` | no | `INBOX` | IMAP mailbox folder name |
```

Then re-run the deterministic gate, which now exits `0`:

```sh
$ python scripts/config/check_config_sync.py
OK
$ echo $?
0
```

By contrast, if the proposal had flagged an **intentional** design choice the
deterministic rules don't encode — e.g. a deliberately commented-out optional
key — you would simply ignore it: no surface edit and no code change is needed.

### Representative text output

```text

Config Drift Advisory
------------------------------------------------------------

imap_folder default mismatch
  confidence: high
  affected field: imap_folder

Docs say INBOX.All but the dataclass default is INBOX.
```

When no drift is detected:

```text

Config Drift Advisory
------------------------------------------------------------
No config drift detected.
```

### JSON output

With `--output-format json`, the output is a single JSON object with a
`proposals` array (empty when no drift is found):

```json
{
  "proposals": [
    {
      "title": "imap_folder default mismatch",
      "body": "Docs say INBOX.All but the dataclass default is INBOX.",
      "affected_field": "imap_folder",
      "confidence": "high"
    }
  ]
}
```

Exit code is `0` on success (even with findings), `1` on error (missing API key,
`pydantic-ai` not installed, or surface read failure).

## The `triage` command

The `triage` subcommand runs an **LLM-driven inbox classifier** that reads
each ingested mail record and assigns it an *action status*:

```sh
$ robotsix-auto-mail triage
```

Each triage decision is stored in the SQLite `triage_decisions` table **and
also moves the card's position on the local kanban board** by writing the
`status` column. The board move is **local-only** — triage performs **zero
IMAP / mailbox operations**. No mail is archived, deleted, moved, or modified
in the original mailbox. The agent defaults uncertain cases to `user_triage`
(explicit deferral to a human) rather than guessing.

### Action statuses and kanban board mapping

| Status | Meaning | Board column |
|---|---|---|
| `answer` | The message needs a personal reply | Needs reply |
| `waiting` | You have already replied or acted; now awaiting a response/action from the other party | Waiting on them |
| `archive` | Keep the message for reference but no reply needed | No action |
| `delete` | The message is junk / worthless and can be discarded | No action |
| `ignore` | No action needed and it need not be kept | Done |
| `user_triage` | The system is not confident — defer to a human | To read |

Each action automatically moves the card to its mapped board column. The
kanban board has five columns — Needs reply, Waiting on them, To read, No action, and Done — and a
triage decision always places the card in one of these five columns. Note
that `delete` moves the card to the No action column (a board move only, not an
IMAP deletion).

### Human-decision memory

The agent learns from your manual triage decisions. When you record a user
decision (via `triage-set`, below), the system remembers that sender's
preference in a persistent, per-sender memory. On future triage runs, the agent
is biased toward repeating those preferences — it treats them as advisory
guidance (not hard rules) so it can still adapt when a message from the same
sender clearly differs in content.

For example, if you've told the system "mail from alice@x.com goes to archive
(3 times)", the prompt will note this preference and the agent will favor
archiving alice's new messages unless context suggests otherwise.

The memory is stored in the SQLite `watermark` table under the key
`triage_human_memory` (alongside other persistent metadata like the archive
structure). It survives across runs and connections.

### Options

| Option | Default | Purpose |
|---|---|---|
| `--api-key` | – | OpenRouter API key; overrides `LLM_API_KEY` env and config file |
| `--output-format` | `text` | Output format: `text` (human-readable) or `json` (machine-readable) |

### Requirements

The `triage` command requires:
- The `pydantic-ai` package (install via `pip install robotsix-auto-mail[dev]`)
- An LLM API key (via `--api-key`, `LLM_API_KEY` env, or `llm.api_key` in config)

### Representative text output

```text

Inbox Triage
------------------------------------------------------------

<a@x.com>
  action: answer
  confidence: high
  reason: Sender is asking a direct question that needs a response.

<b@x.com>
  action: archive
  confidence: high
  reason: Promotional content; keep for reference but no reply needed.

2 message(s) triaged
```

Exit code is `0` on success (even if decisions are produced), `1` on error
(missing API key, `pydantic-ai` not installed, or LLM failure).

## The `triage-set` command

To manually record a triage decision for a single message, use `triage-set`:

```sh
$ robotsix-auto-mail triage-set <message_id> <action>
```

This records the decision in the `triage_decisions` table with `source=user`
(distinguishing it from agent decisions), **moves the card to the mapped
kanban board column** (see the action-to-column table under [Action statuses
and kanban board mapping](#action-statuses-and-kanban-board-mapping)), and
**also updates the human-decision memory ledger**, so future triage runs will
favor that action for mail from the same sender. Like the LLM-driven
`triage` command, this is a local-only board move — no IMAP operations
occur.

### Arguments

| Argument | Purpose |
|---|---|
| `<message_id>` | The Message-ID of the mail to triage (from `board` or `triage` output) |
| `<action>` | The action status: `answer`, `waiting`, `archive`, `delete`, `ignore`, or `user_triage` |

### Examples

```sh
# Record that alice@x.com's message should be archived
robotsix-auto-mail triage-set '<a@x.com>' archive

# Mark a message as needing a reply
robotsix-auto-mail triage-set '<b@x.com>' answer

# Explicitly defer to a human (use this for ambiguous messages)
robotsix-auto-mail triage-set '<c@x.com>' user_triage
```

### Behavior

- If the `message_id` is unknown, exits with code `1` and an error message.
- If the `action` is invalid, exits with code `1` and an error message.
- On success, the decision is stored and the human-decision memory is
  updated; exit code is `0`.

The next `triage` run will treat this sender's preference as advisory guidance
for future mail from the same address.

### Requirements

The `triage-set` command requires a loadable configuration (for `db_path`),
but does **not** require the `pydantic-ai` package or an LLM API key — it is
purely a local decision-recording tool.

## The config-sync-set command

To manually record an operator decision for a single config-drift finding in
the dedup memory ledger, use `config-sync-set`:

```sh
$ robotsix-auto-mail config-sync-set <fingerprint> <state>
```

This writes the chosen `state` (`pending`, `accepted`, or `rejected`) into the
finding's entry in the persisted dedup ledger. See [The dedup memory
ledger](#the-dedup-memory-ledger) and [Ledger state
semantics](#ledger-state-semantics) under the `config-sync` command for how the
ledger is stored in the `watermark` table under the `config_sync_ledger` key,
how per-finding fingerprints are derived, and why all three states suppress
re-reporting equally.

### Arguments

| Argument | Purpose |
|---|---|
| `<fingerprint>` | The per-finding fingerprint of a config-drift proposal, as recorded in the ledger by a prior `config-sync --dedup` run (the same SHA-256 fingerprint described in [Ledger state semantics](#ledger-state-semantics)) |
| `<state>` | The ledger state: `pending`, `accepted`, or `rejected` |

### Examples

```sh
# Accept a drift finding (records state=accepted in the ledger)
robotsix-auto-mail config-sync-set <fingerprint> accepted

# Reject a drift finding
robotsix-auto-mail config-sync-set <fingerprint> rejected
```

### Behavior

The state is written into the `config_sync_ledger` entry inside the SQLite
`watermark` table. Because any recorded state suppresses re-reporting, setting a
finding's state keeps it from resurfacing on future `config-sync --dedup` CLI
runs and `POST /config-sync` endpoint calls (see [The dedup memory
ledger](#the-dedup-memory-ledger) for the full detail).

Error handling:

- If `<state>` is not one of `pending`, `accepted`, or `rejected`, prints an
  `Error: invalid state ...` message listing the valid states and exits with
  code `1`.
- If `<fingerprint>` matches no ledger entry, prints
  `Error: No ledger finding with fingerprint '<fingerprint>'` and exits with
  code `1`.
- If the configuration cannot be loaded, prints an
  `Error loading configuration: ...` message and exits with code `1`.

On success, the state is recorded and exit code is `0`.

### Requirements

The `config-sync-set` command requires:
- A **loadable configuration** (for `db_path`) and an existing SQLite database
  whose `config_sync_ledger` has already been populated by a prior
  `config-sync --dedup` run.
- The **`pydantic-ai` package** (install via
  `pip install robotsix-auto-mail[dev]`); the command's import guard exits `1`
  with an install hint if it is absent. This matches `config-sync`'s
  pydantic-ai requirement.

It does **not** require an LLM API key — unlike `config-sync`, it performs no
LLM call.

## The `auth login` command

To seed the MSAL refresh-token cache for OAuth2 accounts (enabling subsequent
silent token refresh), run the device-code login flow interactively:

```sh
$ robotsix-auto-mail auth login --account <id>
```

### What it does

`auth login` loads the configuration for the specified account, initiates the
OAuth2 device-code flow, and persists the token cache in the account's data
folder. With a single configured account, `--account` may be omitted.

The command:

1. Resolves the account's configuration by `id` (or uses the only configured
   account if exactly one exists and `--account` is omitted).
2. Checks that the account is configured for OAuth2 (`oauth2_provider`
   set — currently only `microsoft` is supported).
3. Prints a verification URL and device code to stderr.
4. Blocks until the user completes device consent in a browser.
5. On success, writes the MSAL token cache to
   `.data/<account-id>/msal_cache.json` and prints the cache path to stdout.

Subsequent token acquisition for that account runs silently (no user
interaction required).

Exit code is `0` on success, `1` on any error (unknown account, non-OAuth2
account, missing `msal` package, device-flow failure, or user abort).

### Examples

```sh
# With a single configured account (--account is optional)
robotsix-auto-mail auth login

# With multiple accounts, specify which one to authenticate
robotsix-auto-mail auth login --account work

# With a non-existent account id
$ robotsix-auto-mail auth login --account nope
Error: Account nope not found. Available ids: ['personal', 'work']
```

### Error handling

- **Unknown account id**: If `--account` names an id that doesn't exist, or if
  multiple accounts are configured and `--account` is omitted, the command
  exits with code `1` and lists the available account ids.
- **Non-OAuth2 account**: If the account has no `oauth2_provider` set (or a
  provider other than `microsoft`), the command exits with code `1` and prints
  a clear message.
- **Missing `msal` package**: If the `msal` library is not installed, the
  command exits with code `1` and prints an install hint:
  `pip install 'robotsix-auto-mail[microsoft]'`.
- **Device-code flow failure**: If the user aborts or the flow encounters an
  error (e.g. network failure), the command exits with code `1` and prints the
  error.

### Requirements

The `auth login` command requires:

- A **loadable configuration** with at least one account and an `oauth2_provider`
  field.
- The **`msal` package**, installed via `pip install 'robotsix-auto-mail[microsoft]'`
  or the `[dev]` extra. The command exits with code `1` and a clear install
  hint if `msal` is not available.

It does **not** require IMAP or SMTP connectivity — authentication is purely
OAuth2-based and happens out-of-band via the device-code flow.

### Integration with `ingest` and `serve`

Once the token cache is seeded, subsequent `ingest` and `serve` commands for
that account will use the cached token automatically. If the token expires,
it is refreshed silently using the refresh token (no user interaction required).
If refresh fails (e.g. the user has revoked the app's consent), a new
device-code login is required.
