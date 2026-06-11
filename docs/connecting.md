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

# Optional: choose a different model (default: deepseek/deepseek-v4-flash)
export LLM_MODEL=anthropic/claude-3-haiku
```

Instead of environment variables, you can put these in the `llm:` section of
`config/mail.local.yaml` (see [Configuration keys](#configuration-keys)). The
LLM credentials resolve through the same cascade as everything else — the
`LLM_API_KEY` / `LLM_MODEL` environment variables override the file. The same
settings will be reused by future LLM-assisted mail processing, not just
`detect`.

### Minimal usage

```sh
robotsix-auto-mail detect user@gmail.com
```

This auto-detects settings, prompts for the password interactively, writes a
single `config/mail.local.yaml` with the password included, and then verifies
the settings by connecting to the IMAP and SMTP servers (the same check as the
`probe` command). Pass `--no-verify` to skip that connection check.

Re-running `detect` over an existing file updates the mail fields but
preserves the `llm:` section, so your API key is not lost.

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
#   model: deepseek/deepseek-v4-flash
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
| `store.path` | no | `".data/mail.db"` | Filesystem path for the SQLite database |
| `ingest.interval_minutes` | no | `15` | Minutes between automatic ingest cycles (`ingest --watch`) |
| `archive.root` | no | `"robotsix-mail-archive"` | Root folder for the self-managed archive structure |
| `archive.namespace` | no | `""` | IMAP namespace prefix for archive folders (e.g. `"INBOX."`) |
| `archive.enabled` | no | `true` | Whether to create/manage the archive folder structure |
| `triage.on_ingest` | no | `true` | Whether to run the inbox triage agent automatically after each ingest |
| `llm.api_key` | no | – | LLM provider API key for `detect` / mail processing (may instead be supplied via `LLM_API_KEY`) |
| `llm.model` | no | `"deepseek/deepseek-v4-flash"` | LLM model name |
| `langfuse.public_key` | no | – | Langfuse public key; when set with the secret key, every LLM agent run is traced |
| `langfuse.secret_key` | no | – | Langfuse secret key (redacted in logs/repr) |
| `langfuse.base_url` | no | – | Langfuse host URL (falls back to llmio's own default when unset) |

The `auth.password` and `llm.api_key` values are **redacted** in logs and
debug output regardless of how they are supplied.

Setting `langfuse.public_key` / `langfuse.secret_key` (or the matching
`MAIL_LANGFUSE_*` env vars) enables Langfuse tracing for every LLM-running
subcommand. Since `config/mail.local.yaml` is git-ignored, the deployment
supplies the real keys there without committing them.

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
| `MAIL_CONFIG_PATH` | no | `config/mail.local.yaml` | Filesystem path to the YAML config file |
| `LLM_API_KEY` | no | – | LLM provider API key (overrides `llm.api_key`); required for `detect` |
| `LLM_MODEL` | no | `deepseek/deepseek-v4-flash` | LLM model name (overrides `llm.model`) |
| `MAIL_LANGFUSE_PUBLIC_KEY` | no | – | Langfuse public key (overrides `langfuse.public_key`); enables LLM tracing |
| `MAIL_LANGFUSE_SECRET_KEY` | no | – | Langfuse secret key (overrides `langfuse.secret_key`; redacted) |
| `MAIL_LANGFUSE_BASE_URL` | no | – | Langfuse host URL (overrides `langfuse.base_url`) |
| `LOG_LEVEL` | no | `INFO` | Minimum log level — one of `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | no | `console` | Log renderer — `json` for structured logs, `console` for human-friendly dev output |

**TLS modes**

| Mode | Behaviour |
|---|---|
| `direct-tls` | TLS from the first byte, no plaintext negotiation (IMAP port 993, SMTP port 465) |
| `starttls` | Plain connection upgraded to TLS via STARTTLS (IMAP port 143, SMTP port 587) |
| `none` | No TLS at all — **insecure, for local development only** |

### OAuth2 (XOAUTH2)

Gmail deprecated password-based IMAP/SMTP auth in March 2025, and Microsoft 365
has also deprecated basic auth. These providers (and others) now require
**SASL XOAUTH2** — an industry-standard OAuth2-based SASL mechanism.

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

**Microsoft 365 / Outlook.com:**

1. Register an application in the
   [Azure Portal](https://portal.azure.com/) under "App registrations".
2. Under "API permissions", add the ``IMAP.AccessAsUser.All`` and
   ``SMTP.Send`` delegated permissions.
3. Use the OAuth2 device-code or authorization-code flow with scopes
   ``https://outlook.office.com/IMAP.AccessAsUser.All`` and
   ``https://outlook.office.com/SMTP.Send`` (or the combined
   ``https://outlook.office.com/.default`` for both).
4. Set the resulting access token as ``auth.oauth2_token``.

**Self-hosted / other providers:**

If your IMAP/SMTP server still accepts password-based ``login()``, leave the
OAuth2 fields unset — the existing password path works as before.

### Multiple accounts

`robotsix-auto-mail` can manage more than one mailbox at once. Multiple
accounts are modelled as N independent configurations — each account is a
complete set of the connection settings described above, plus a stable
`id` and an optional human-friendly `label`. The single-account shapes
documented above continue to work unchanged; multi-account is purely
additive.

**One SQLite DB per account.** Rather than tagging every database row with an
`account_id`, each account carries its **own** `store.path` (SQLite database
file). Per-account state (triage decisions, sender memory, archive
watermarks) is therefore naturally isolated with zero schema changes, and the
existing per-config `store.path` is the only plumbing needed. The cost is one
SQLite file per account, so every account's `store.path` must be unique —
uniqueness is enforced when the configuration loads. When an account omits
`store.path`, it defaults to `.data/mail-<id>.db`, which is unique per
account (the single-account default stays `.data/mail.db`).

**YAML shape.** A multi-account YAML file uses a top-level `accounts:` list
instead of the single-account top-level sections. Each list entry is a
mapping with a required string `id`, an optional `label`, and the usual
nested `imap` / `smtp` / `auth` / `store` (and optional `llm` / `ingest` /
`archive` / `triage`) sections — parsed exactly as in the single-account
file. An optional top-level `default_account:` names the default account;
when omitted, the first entry is the default. A complete example ships in
`config/mail.accounts.example.yaml`:

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
      path: .data/mail-personal.db

  - id: work
    label: Work mailbox
    imap:
      host: imap.work.example.com
    smtp:
      host: smtp.work.example.com
    auth:
      username: me@work.example.com
    store:
      path: .data/mail-work.db
```

**Environment-variable scheme.** Each per-field environment variable is
namespaced per account by inserting `ACCOUNTS_<n>_` after `MAIL_`, where `<n>`
is a zero-based account index. A field whose single-account variable is
`MAIL_<X>` becomes `MAIL_ACCOUNTS_<n>_<X>` (for example
`MAIL_ACCOUNTS_0_IMAP_HOST`, `MAIL_ACCOUNTS_1_PASSWORD`); the two LLM fields
become `MAIL_ACCOUNTS_<n>_LLM_API_KEY` / `MAIL_ACCOUNTS_<n>_LLM_MODEL`. Two
extra namespaced variables describe the account itself: `MAIL_ACCOUNTS_<n>_ID`
(required — the stable account id, e.g. `MAIL_ACCOUNTS_0_ID=personal`) and
`MAIL_ACCOUNTS_<n>_LABEL` (optional). Account indices must be contiguous
starting at 0 (a gap raises an error). An optional `MAIL_ACCOUNTS_DEFAULT`
names the default account id. As with `store.path` in YAML, an account whose
`MAIL_ACCOUNTS_<n>_DB_PATH` is unset defaults to `.data/mail-<id>.db`.

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

**LLM settings** (`llm.api_key` / `llm.model`) follow the same rule —
`LLM_API_KEY` / `LLM_MODEL` override the file's `llm:` section. The `detect`
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

**Rule proposals section.**  Above the board, a **Rule proposals**
section displays any pending deterministic-rule proposals waiting for human
validation. Each proposal shows:
- the human-readable title (e.g. "Triage mail from alice@example.com as archive")
- a rule summary (`match_type=value -> action`)
- **Accept** and **Reject** buttons

Click **Accept** to activate the rule so future matching mail is triaged
deterministically; click **Reject** to discard it. Accepting a rule moves it to
the active-rules list and triggers an immediate 302 redirect back to `/board`.
The section shows a count badge and displays "No pending rule proposals" when
the queue is empty. Proposals are generated by the `triage-rules` CLI command or
the `POST /rule-action` endpoint and stored in the database's rule ledger; the
board displays them read-only and offers no way to *generate* new proposals (that
remains a CLI / scheduled-task responsibility).

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

### The `/rule-action` endpoint

In addition to the rule proposals displayed on the board page, the server
accepts `POST /rule-action` to validate (accept or reject) a pending triage
rule proposal. This endpoint is used by the board UI's **Accept** and **Reject**
buttons, but can also be called directly by external tools or scripts.

#### Request

```sh
curl -X POST http://localhost:8080/rule-action \
  -d 'fingerprint=<fingerprint>&decision=accept'
```

Form-encoded body parameters:
- `fingerprint` (required): The SHA-256 fingerprint of the rule proposal (as shown in the board or by `triage-rules` CLI output)
- `decision` (required): Either `accept` or `reject`

#### Response on success (HTTP 302)

A 302 redirect to `/board` (the decision has been recorded and the page
auto-refreshes to show the updated state).

#### Error responses

- **HTTP 400** — Missing or invalid `fingerprint` / `decision`, or an unrecognized `decision` value
- **HTTP 404** — Unknown fingerprint (the proposal does not exist in the ledger)

#### Behavior

- **`decision=accept`**: transitions the proposal to `accepted` state, adds the
  underlying rule to the active-rules list so future matching mail is triaged
  deterministically, and redirects to `/board`.
- **`decision=reject`**: transitions the proposal to `rejected` state (the rule
  is not activated) and redirects to `/board`.

Both decisions suppress the proposal from resurfacing on future `triage-rules`
CLI runs or board refreshes.

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

## The `triage-folder` command

The `triage-folder` subcommand is a **one-shot** operation that fetches every
message from a named mailbox folder that is **not** the configured INBOX (e.g.
a legacy `Archive`, `Sent`, or a custom label), stores them locally, and then
runs the triage agent over the newly-stored mail:

```sh
$ robotsix-auto-mail triage-folder Archive
```

Unlike the incremental `ingest` cycle, this performs a **full sweep** of the
named folder (IMAP search `ALL`) and:

- stores new mail locally, **deduplicating by Message-ID** (re-running the
  command stores `0` new records), and
- **leaves the INBOX ingest watermark completely untouched** — it is not read
  or written, so the normal incremental INBOX ingest is unaffected.

Triage remains **advisory and local-only**: no mail is moved, deleted, or
copied in the IMAP mailbox.

### Arguments

| Argument | Purpose |
|---|---|
| `<folder>` | The IMAP folder/mailbox name to triage (e.g. `Archive`) |

### Options

| Option | Default | Purpose |
|---|---|---|
| `--account` | – | Account id to operate on; optional when only one account is configured |
| `--api-key` | – | OpenRouter API key; overrides `LLM_API_KEY` env and config file |
| `--output-format` | `text` | Output format: `text` (human-readable) or `json` (machine-readable) |
| `--dry-run` | `false` | Fetch and parse without storing; skip the triage pass |

### Requirements

The `triage-folder` command requires:
- The `pydantic-ai` package (install via `pip install robotsix-auto-mail[dev]`)
- An LLM API key (via `--api-key`, `LLM_API_KEY` env, or `llm.api_key` in config)

### Representative text output

```text
Fetched:  3 messages
Stored:   3 new
Skipped:  0 duplicate
Errors:   0

Folder Triage
------------------------------------------------------------

<a@x.com>
  action: archive
  confidence: high
  reason: Old reference mail; keep but no reply needed.
```

Exit code is `0` on success (even if decisions are produced), `1` on error
(missing API key, `pydantic-ai` not installed, `ImapError`, or LLM failure).

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

## The `triage-rules` command

Beyond per-message LLM classification, the system can propose **deterministic
triage rules** derived from your recorded triage history and apply accepted
rules without any LLM call. To see the latest proposals (and the rules already
accepted), run:

```sh
$ robotsix-auto-mail triage-rules
```

### How it works

`triage-rules` scans the `triage_decisions` history (no LLM involved) and
proposes a rule whenever the evidence is consistent and above a small
threshold:

- **Sender rule** — a single sender that was triaged the same non-`user_triage`
  way at least three times (for example `newsletters@x.com` archived three
  times) becomes a proposal to triage that sender's mail with that action.
- **Domain rule** — when at least two distinct senders in one domain were all
  triaged the same way, and the domain accumulated at least three such
  decisions **in total** (domain-wide, not three per sender — e.g.
  `alice@news.com` and `bob@news.com` archived twice each, four decisions
  combined), a domain-level proposal is emitted.

`user_triage` decisions are ignored entirely, and a sender (or domain) with
conflicting actions yields no proposal. Each new proposal is recorded in a
dedup memory ledger keyed by a stable SHA-256 fingerprint over
`(match_type, match_value, action)`, so a finding already seen in any
state — `pending`, `accepted`, or `rejected` — is never re-proposed.

### Options

| Option | Description |
| --- | --- |
| `--output-format {text,json}` | Output format for proposals and active rules (default: `text`). |

### Behavior

This is an **advisory** tool: it always exits `0`, even when proposals are
found. The text output lists each new proposal with its fingerprint,
confidence and rule, followed by the currently active (accepted) rules. The
JSON output is an object with `proposals` (each carrying its `fingerprint`)
and `active_rules`.

### Requirements

The `triage-rules` command requires a loadable configuration (for `db_path`)
but does **not** require the `pydantic-ai` package or an LLM API key — rule
proposal is purely deterministic.

## The `triage-rules-set` command

To accept or reject a proposed triage rule, you can use either the **web board**
(click **Accept** or **Reject** on any rule proposal card in the **Rule
proposals** section) or the CLI command:

```sh
$ robotsix-auto-mail triage-rules-set <fingerprint> <state>
```

Accepting a proposal adds its rule to the **active rules** list (persisted in
the watermark table under `triage_rules_active`). On subsequent `triage` runs,
each inbox message is checked against the active rules first: a match is
triaged deterministically (recorded with `source=agent` and
`reason="matched deterministic rule"`) and is **not** sent to the LLM; only
unmatched mail is classified by the agent. Rejecting a proposal records the
decision in the ledger without adding an active rule.

### Arguments

| Argument | Description |
| --- | --- |
| `<fingerprint>` | The fingerprint of a rule proposal, as printed by a prior `triage-rules` run. |
| `<state>` | `accepted` or `rejected`. |

### Examples

```sh
# Accept a proposed rule so matching mail is triaged deterministically.
robotsix-auto-mail triage-rules-set <fingerprint> accepted

# Reject a proposed rule so it is suppressed without becoming active.
robotsix-auto-mail triage-rules-set <fingerprint> rejected
```

### Behavior

- If the `fingerprint` is unknown, exits with code `1` and an error message.
- If the `state` is not `accepted` or `rejected`, exits with code `1` and an
  error message.
- On success, the ledger (and, for `accepted`, the active-rules list) is
  updated; exit code is `0`.

### Requirements

The `triage-rules-set` command requires a loadable configuration (for
`db_path`) but does **not** require the `pydantic-ai` package or an LLM API
key — it is purely a local decision-recording tool.

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
