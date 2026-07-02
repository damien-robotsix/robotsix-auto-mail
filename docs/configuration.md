# Configuration Reference

`robotsix-auto-mail` is configured through **built-in defaults overlaid by a
single YAML config file**. Each field the YAML file supplies overrides its
built-in default; any field you omit keeps its default.

> **Configuration is provided primarily via the YAML config file.** Three
> environment variables are consulted:
>
> - `MAIL_CONFIG_PATH` — locates the YAML config file (default
>   `config/mail.local.yaml`).
> - `LLM_API_KEY` — LLM API key fallback (read by `resolve_llm_api_key` in
>   `config/loader.py`).
> - `LLM_PROVIDER_MODEL` — LLM model/provider fallback.

For a guided setup and the `detect` auto-configuration command, see
[Connecting](connecting.md). The canonical template ships in
[`docs/config/mail.local.example.yaml`](config/mail.local.example.yaml).

---

## Config file location

The loader reads the YAML file at the path given by `MAIL_CONFIG_PATH`
(default `config/mail.local.yaml`). `MAIL_CONFIG_PATH` only *points at* the
file — it carries no configuration values itself.

| Environment variable | Default | Purpose |
|---|---|---|
| `MAIL_CONFIG_PATH` | `config/mail.local.yaml` | Filesystem path used to locate the YAML config file. |

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
  `archive`, `triage`, and `component_agent` — live under each `accounts:`
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

### `component_agent`

Optional HTTP API (monitor / config-get / config-set) served on the board
server, letting external agents inspect status and read/apply configuration
over HTTP — without the agent-comm broker. This is a **per-account** field.

| Key | Default | Kind | Required | Description |
|---|---|---|---|---|
| `component_agent.enabled` | `false` | boolean | no | Whether the component-agent HTTP API is served on the board server. Accepts `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off`. |

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
| `logging.file_dir` | `.mail_log` | string | no | Directory for date-stamped debug log files (`mail-YYYY-MM-DD.log`). An empty or whitespace-only value disables file logging. |

---

## Accounts container

| Key | Default | Kind | Required | Description |
|---|---|---|---|---|
| `accounts` | *(none)* | list | yes | List of per-account mappings. Each entry requires a stable `id` and the `imap` / `smtp` / `auth` sections above; other per-account sections are optional. |
| `accounts[].id` | *(none)* | string | yes | Stable, filesystem/URL-safe identifier for the account (e.g. `personal`). Must match `^[A-Za-z0-9._-]+$` and be unique across accounts. |
| `accounts[].label` | *(none)* | string | no | Human-friendly display name (e.g. `Personal Gmail`). |
| `default_account` | *(first account)* | string | no | The `id` of the default account for CLI operations. When absent, the first `accounts:` entry is the default. |

Rules enforced when the file loads:

- At least one account is required; every account needs a unique, non-empty
  `id`.
- Every account's resolved `store.path` must be unique (one SQLite database per
  account).
- `default_account`, when set, must name an existing account `id`.

---

## Related pages

- [Connecting](connecting.md) — guided setup, YAML config structure, and the
  `detect` auto-configuration command.
- [Deployment](deployment.md) — Docker Compose setup, container build, and
  production operations.
