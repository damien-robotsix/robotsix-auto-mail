# Configuration Reference

`robotsix-auto-mail` is configured through **built-in defaults overlaid by a
single YAML config file**. Each field the YAML file supplies overrides its
built-in default; any field you omit keeps its default.

> **Configuration is provided primarily via the YAML config file.** Three
> environment variables are consulted:
>
> - `ROBOTSIX_CONFIG_FILE` — locates the YAML config file (default
>   `config/config.json`).
> - `LLM_API_KEY` — LLM API key fallback (read by `resolve_llm_api_key` in
>   `config/loader.py`).
> - `LLM_PROVIDER_MODEL` — LLM model/provider fallback.

For a guided setup and the `detect` auto-configuration command, see
[Connecting](connecting.md). The canonical template ships in
[`config/config.example.json`](../config/config.example.json).

---

## Config file location

The loader reads the YAML file at the path given by `ROBOTSIX_CONFIG_FILE`
(default `config/config.json`). `ROBOTSIX_CONFIG_FILE` only *points at* the
file — it carries no configuration values itself.

| Environment variable | Default | Purpose |
|---|---|---|
| `ROBOTSIX_CONFIG_FILE` | `config/config.json` | Filesystem path used to locate the YAML config file. |

---

## File shape

The config file has a top-level `accounts:` list — one entry per mailbox — plus
optional application-wide `llm:`, `langfuse:`, and `logging:` sections and an
optional `default_account:` key.

```yaml
# Application-wide (top-level) sections
llm:
  api_key: sk-or-v1-…
langfuse:
  public_key: ""
logging:
  level: INFO

# The default account for CLI operations (absent → the first account below)
default_account: personal

accounts:
  - id: personal          # required, stable, filesystem/URL-safe id
    label: Personal Gmail # optional human-friendly display name
    imap:
      host: imap.gmail.com
    smtp:
      host: smtp.gmail.com
    auth:
      username: me@gmail.com
      password: ""
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
      password: ""
    store:
      path: .data/work/mail.db
```

- **Per-account sections** — `imap`, `smtp`, `auth`, `store`, `ingest`,
  `archive`, and `triage` — live under each `accounts:`
  entry.
- **Application-wide sections** — `llm`, `langfuse`, and `logging` — are
  top-level and apply to every account.
- The single-account ("mono") shape (top-level `imap:` / `smtp:` / `auth:`
  with no `accounts:` key) is **no longer loaded**. Run
  edit the `accounts:` block directly, or run
  `robotsix-auto-mail detect` to regenerate it.

---

## Per-account sections

### `imap` — incoming mail

| Key | Default | Kind | Required | Description |
|---|---|---|---|---|
| `imap.host` | *(none)* | string | yes | Hostname of the IMAP server. |
| `imap.port` | `993` | integer | no | IMAP server port. |
| `imap.tls_mode` | `direct-tls` | `starttls` / `direct-tls` / `none` | no | TLS negotiation mode. `direct-tls` initiates TLS immediately (port 993 convention); `starttls` upgrades after connecting (port 143 convention); `none` disables TLS entirely. |
| `imap.folder` | `INBOX` | string | no | Mailbox (folder) to watch for new mail. |

### `smtp` — outgoing mail

| Key | Default | Kind | Required | Description |
|---|---|---|---|---|
| `smtp.host` | *(none)* | string | yes | Hostname of the SMTP server. |
| `smtp.port` | `587` | integer | no | SMTP server port. |
| `smtp.tls_mode` | `starttls` | `starttls` / `direct-tls` / `none` | no | TLS negotiation mode. |

### `auth` — authentication

| Key | Default | Kind | Required | Description |
|---|---|---|---|---|
| `auth.username` | *(none)* | string | yes | Login username — typically the full email address. |
| `auth.password` | *(none)* | string | no | Login password. Masked in logs and `repr`. Not required when `auth.oauth2_provider` is `microsoft` (MSAL acquires tokens instead). |
| `auth.oauth2_token` | `""` | string | no | OAuth2 access token for SASL XOAUTH2. When set, password-based `login()` is skipped. |
| `auth.oauth2_client_id` | `""` | string | no | OAuth2 client identifier — required by some providers alongside the token. |
| `auth.oauth2_client_secret` | `""` | string | no | OAuth2 client secret. Masked in logs and `repr`. |
| `auth.oauth2_provider` | `""` | string | no | MSAL OAuth2 provider. Set to `microsoft` to acquire and refresh tokens via MSAL instead of password auth. |
| `auth.oauth2_tenant` | `organizations` | string | no | Azure AD tenant for MSAL-managed OAuth2. |

### `store` — storage

| Key | Default | Kind | Required | Description |
|---|---|---|---|---|
| `store.path` | `""` | string | no | Path to the SQLite database file. When empty, the per-account default `.data/<id>/mail.db` is derived (unique per account). Every account must resolve to a distinct path. |

### `ingest` — automatic ingestion

| Key | Default | Kind | Required | Description |
|---|---|---|---|---|
| `ingest.interval_minutes` | `15` | integer | no | Minutes between automatic ingest cycles when running `ingest --watch`. |
| `ingest.mode` | `"once"` | `"watch"` / `"once"` | no | Start-up behaviour: `"watch"` runs the ingest loop continuously; `"once"` exits after a single pass. The entrypoint reads this field when no CLI command is given. |
| `ingest.heartbeat_file` | `""` | string | no | Filesystem path touched at the end of each poll cycle in `"watch"` mode so a Docker HEALTHCHECK can verify liveness. An empty string disables the heartbeat file. |

### `archive`

| Key | Default | Kind | Required | Description |
|---|---|---|---|---|
| `archive.root` | `robotsix-mail-archive` | string | no | Root folder under which the self-managed archive structure lives. |
| `archive.enabled` | `true` | boolean | no | Whether to create and manage the archive folder structure. Accepts `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off`. |

### `triage` — inbox triage

| Key | Default | Kind | Required | Description |
|---|---|---|---|---|
| `triage.on_ingest` | `true` | boolean | no | Whether to run the inbox triage agent automatically after each ingest cycle. Accepts `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off`. |
| `triage.rules_path` | `""` | string | no | Path to the human-readable `triage_rules.md` the flash LLM maintains from board actions. When empty, `<db-dir>/triage_rules.md` is derived from `store.path`. |

---

## Application-wide (top-level) sections

These sections are **not** nested under `accounts:` — they apply to every
account.

### `llm` — LLM provider

Used by the `detect` subcommand and future LLM-assisted mail processing.

| Key | Default | Kind | Required | Description |
|---|---|---|---|---|
| `llm.api_key` | `""` | string | no | OpenRouter API key (or provider-specific key). Get one at <https://openrouter.ai/keys>. Masked in logs and `repr`. |
| `llm.provider_model` | `""` | string | no | LLM backend name. When empty, the `robotsix-llmio` library's tier default is used. See its README for available backends. |

### `langfuse` — tracing

When both `langfuse.public_key` and `langfuse.secret_key` are set, every LLM
agent run is traced to the configured Langfuse project.

| Key | Default | Kind | Required | Description |
|---|---|---|---|---|
| `langfuse.public_key` | `""` | string | no | Public key from your Langfuse project settings. |
| `langfuse.secret_key` | `""` | string | no | Secret key from your Langfuse project settings. Masked in logs and `repr`. |
| `langfuse.base_url` | `""` | string | no | Langfuse host override. When empty, the `robotsix-llmio` library default (`https://cloud.langfuse.com`) is used. |

### `logging` — observability

| Key | Default | Kind | Required | Description |
|---|---|---|---|---|
| `logging.level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` | no | Minimum log level. |
| `logging.format` | `console` | `json` / `console` | no | Log renderer. `json` for structured production logs; `console` for human-friendly development output. |

---

## Accounts container

| Key | Default | Kind | Required | Description |
|---|---|---|---|---|
| `accounts` | *(none)* | list | yes | List of per-account mappings. Each entry requires a stable `id` and the `imap` / `smtp` / `auth` sections above; other per-account sections are optional. |
| `accounts[].id` | *(none)* | string | yes | Stable, filesystem/URL-safe identifier for the account (e.g. `personal`). Must match `^[A-Za-z0-9._-]+$` and be unique across accounts. |
| `accounts[].label` | *(none)* | string | no | Human-friendly display name (e.g. `Personal Gmail`). |
| `default_account` | *(first account)* | string | no | The `id` of the default account for CLI operations. When absent, the first `accounts:` entry is the default. |

Rules enforced when the file loads:

- Every account needs a unique, non-empty `id`.
- An empty `accounts: []` is valid — fresh deploys start with zero accounts
  configured. The server boots with an in-memory database and the add-account
  form is available to create accounts through the web UI.
- Every account's resolved `store.path` must be unique (one SQLite database per
  account).
- `default_account`, when set, must name an existing account `id`.

---

## Runtime settings API

The board server exposes a per-component settings API at ``/settings`` that
reads and writes the **internal settings store** (a ``component_settings``
table in the per-account SQLite database).  Once the store is populated —
either via a one-time import from central-deploy on first boot or via the
``PUT /settings`` endpoint — runtime configuration is fully self-owned by
the component; no central-deploy call is required to change a runtime
setting.

The browser-side surface for *account* management (as distinct from
per-component runtime settings) is the **Settings panel** page served at
``/settings-panel``, linked from the board header.  It lists every
configured mail account and lets an operator delete one via
``POST /delete-account``, which removes the account from the persisted
``config/config.json`` (see `docs/connecting.md` → "The board page").

### GET /settings

Returns all component settings as a JSON object with secrets masked as
``"***"``.  The response includes a ``source`` field:

- ``"internal"`` — settings come from the internal store (the
  ``component_settings`` table).
- ``"config-file"`` — the store is empty (no import has run yet); values
  are derived from the in-memory config file with secrets masked.

```json
{
  "settings": {
    "imap_host": "imap.gmail.com",
    "password": "***",
    "username": "me@gmail.com",
    "db_path": ".data/personal/mail.db"
  },
  "source": "internal"
}
```

### PUT /settings

Accepts a JSON object with one or more field names and their new values.
Each field is validated against the :class:`MailConfig` model before being
persisted.  On partial failure, valid fields are still written — the
``errors`` map lists only the rejected keys.

**Request** (partial update):
```json
{"imap_host": "new-imap.example.com", "ingest_interval_minutes": "10"}
```

**Response** (all valid):
```json
{"ok": true, "errors": {}}
```

**Response** (partial failure):
```json
{"ok": false, "errors": {"bad_field": "unknown setting: 'bad_field'"}}
```

### One-time import (first boot)

When the ``CENTRAL_DEPLOY_EXPORT_URL`` environment variable is set at
container boot time, the board server calls central-deploy's config-export
endpoint on first startup and seeds the internal settings store.  The
import is **idempotent** — it only runs when the store is empty, so
restarting the service never overwrites locally-edited settings.

---

## Related pages

- [Connecting](connecting.md) — guided setup, YAML config structure, and the
  `detect` auto-configuration command.
- [Deployment](deployment.md) — Docker Compose setup, container build, and
  production operations.
