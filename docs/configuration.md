# Configuration Reference

`robotsix-auto-mail` is configured through a three-layer cascade:
**built-in defaults → YAML config file → environment variables** —
each layer overrides the previous one field by field.

This page documents every environment variable accepted by the
application, grouped by category. For the YAML file counterpart and
a guided setup, see [Connecting](connecting.md).

---

## Configuration cascade

At startup, fields are resolved in this order:

1. **Built-in defaults** — safe, minimal defaults are baked into the
   `MailConfig` dataclass and the `_FIELD_SPECS` table (see
   `src/robotsix_auto_mail/config/`).
2. **YAML config file** — `config/mail.local.yaml` (when
   `MAIL_CONFIG_PATH` points at it) overrides defaults. See
   `docs/config/mail.local.example.yaml` for the template.
3. **Environment variables** — `MAIL_*` vars (and global `LLM_*`,
   `LANGFUSE_*`, etc.) override both defaults and the YAML file.

Each layer sets only the fields it supplies; missing fields fall through
to the next source below it.

---

## Variable categories

### Single-account vs multi-account

The variables listed below describe a **single account**. To drive
several mailboxes from one process, use the namespaced scheme:
prefix every per-account variable with `MAIL_ACCOUNTS_<n>_` where `<n>`
is a zero-based, contiguous integer index. Global variables (LLM,
Langfuse, logging, board agent) are read from the bare names below —
they are never namespaced.

See [Multi-account variables](#multi-account-variables) for details.

---

## IMAP (incoming mail)

| Variable | Default | Kind | Required | Description |
|---|---|---|---|---|
| `MAIL_IMAP_HOST` | *(none)* | string | yes | Hostname of the IMAP server. |
| `MAIL_IMAP_PORT` | `993` | integer | no | IMAP server port. |
| `MAIL_IMAP_TLS_MODE` | `direct-tls` | `starttls` / `direct-tls` / `none` | no | TLS negotiation mode. `direct-tls` initiates TLS immediately (port 993 convention); `starttls` upgrades after connecting (port 143 convention); `none` disables TLS entirely. |
| `MAIL_IMAP_FOLDER` | `INBOX` | string | no | Mailbox (folder) to watch for new mail. |

---

## SMTP (outgoing mail)

| Variable | Default | Kind | Required | Description |
|---|---|---|---|---|
| `MAIL_SMTP_HOST` | *(none)* | string | yes | Hostname of the SMTP server. |
| `MAIL_SMTP_PORT` | `587` | integer | no | SMTP server port. |
| `MAIL_SMTP_TLS_MODE` | `starttls` | `starttls` / `direct-tls` / `none` | no | TLS negotiation mode. |

---

## Authentication

| Variable | Default | Kind | Required | Description |
|---|---|---|---|---|
| `MAIL_USERNAME` | *(none)* | string | yes | Login username — typically the full email address. |
| `MAIL_PASSWORD` | *(none)* | string | yes | Login password. Masked in logs and `repr`. |
| `MAIL_OAUTH2_TOKEN` | `""` | string | no | OAuth2 access token for SASL XOAUTH2. When set, password-based `login()` is skipped. |
| `MAIL_OAUTH2_CLIENT_ID` | `""` | string | no | OAuth2 client identifier — required by some providers alongside the token. |
| `MAIL_OAUTH2_CLIENT_SECRET` | `""` | string | no | OAuth2 client secret. Masked in logs and `repr`. |
| `MAIL_OAUTH2_PROVIDER` | `""` | string | no | MSAL OAuth2 provider. Set to `"microsoft"` to acquire and refresh tokens via MSAL instead of password auth. |
| `MAIL_OAUTH2_TENANT` | `organizations` | string | no | Azure AD tenant for MSAL-managed OAuth2. |

---

## Storage

| Variable | Default | Kind | Required | Description |
|---|---|---|---|---|
| `MAIL_DB_PATH` | `.data/mail.db` | string | no | Path to the SQLite database file. In multi-account mode, when omitted the default per-account path is `.data/<account-id>/mail.db`. |

---

## Automatic ingestion

| Variable | Default | Kind | Required | Description |
|---|---|---|---|---|
| `MAIL_INGEST_INTERVAL` | `15` | integer | no | Minutes between automatic ingest cycles when running `ingest --watch`. |

---

## Archive

| Variable | Default | Kind | Required | Description |
|---|---|---|---|---|
| `MAIL_ARCHIVE_ROOT` | `robotsix-mail-archive` | string | no | Root folder under which the self-managed archive structure lives. |
| `MAIL_ARCHIVE_ENABLED` | `true` | boolean | no | Whether to create and manage the archive folder structure. Accepts `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off`. |
| `MAIL_ARCHIVE_NAMESPACE` | `""` | string | no | IMAP namespace prefix for archive folders (e.g. `"INBOX."` for Dovecot servers with `namespace/inbox=yes`). |

---

## Inbox triage

| Variable | Default | Kind | Required | Description |
|---|---|---|---|---|
| `MAIL_TRIAGE_ON_INGEST` | `true` | boolean | no | Whether to run the inbox triage agent automatically after each ingest cycle. Accepts `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off`. |

---

## Calendar (Add to Calendar)

| Variable | Default | Kind | Required | Description |
|---|---|---|---|---|
| `MAIL_CALENDAR_ENABLED` | `true` | boolean | no | Whether the "Add to Calendar" button appears in the mail detail view and dispatches event requests to the `robotsix-calendar` agent. Accepts `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off`. |
| `CALENDAR_TRANSPORT` | `in-process` | `in-process` / `brokered` | no | Transport mode for calendar dispatch. `in-process` uses a local agent registry; `brokered` connects to the secured broker server over TLS with token auth. |
| `CALENDAR_BROKER_HOST` | `""` | string | no | Broker server hostname. Required when `CALENDAR_TRANSPORT=brokered`. |
| `CALENDAR_BROKER_PORT` | `443` | integer | no | Broker server port. |
| `CALENDAR_BROKER_TLS_CA` | `""` | string | no | Path to the CA certificate PEM for verifying the broker's TLS certificate. Required when `CALENDAR_TRANSPORT=brokered`. |
| `CALENDAR_BROKER_CLIENT_CERT` | `""` | string | no | Path to the client certificate PEM for mutual TLS (optional). |
| `CALENDAR_BROKER_CLIENT_KEY` | `""` | string | no | Path to the client key PEM for mutual TLS (optional). |
| `CALENDAR_BROKER_TOKEN` | `""` | string | no | Agent authentication token for the broker. Required when `CALENDAR_TRANSPORT=brokered`. Masked in logs and `repr`. |

---

## LLM provider (global)

Used by the `detect` subcommand and future LLM-assisted mail processing.
These are application-wide — they are **not** namespaced in multi-account
mode.

| Variable | Default | Kind | Required | Description |
|---|---|---|---|---|
| `LLM_API_KEY` | `""` | string | no | OpenRouter API key (or provider-specific key). Get one at https://openrouter.ai/keys. Masked in logs and `repr`. |
| `LLM_PROVIDER_MODEL` | `openrouter-deepseek` | string | no | LLM backend name. See `robotsix-llmio`'s README for available backends. |

---

## Langfuse tracing (global)

When both `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set, every
LLM agent run is traced to the configured Langfuse project. These are
application-wide — they are **not** namespaced in multi-account mode.

| Variable | Default | Kind | Required | Description |
|---|---|---|---|---|
| `LANGFUSE_PUBLIC_KEY` | `""` | string | no | Public key from your Langfuse project settings. |
| `LANGFUSE_SECRET_KEY` | `""` | string | no | Secret key from your Langfuse project settings. Masked in logs and `repr`. |
| `LANGFUSE_BASE_URL` | `""` | string | no | Langfuse host override. When empty, the `robotsix-llmio` library default (`https://cloud.langfuse.com`) is used. |

---

## Logging / observability (global)

| Variable | Default | Kind | Required | Description |
|---|---|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` | no | Minimum log level. |
| `LOG_FORMAT` | `console` | `json` / `console` | no | Log renderer. `json` for structured production logs; `console` for human-friendly development output. |
| `LOG_FILE_DIR` | `.mail_log` | string | no | Directory for date-stamped debug log files (`mail-YYYY-MM-DD.log`). An empty or whitespace-only value disables file logging. |

---

## Board agent (global)

Optional agent-comm bridge to the mill board. When enabled, other agents
can drive the board programmatically via agent-comm messages. These are
application-wide — they are **not** namespaced in multi-account mode.

| Variable | Default | Kind | Required | Description |
|---|---|---|---|---|
| `BOARD_AGENT_ENABLED` | `false` | boolean | no | Enable the board agent bridge. Accepts `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off`. |
| `BOARD_AGENT_API_URL` | `""` | string | no | Base URL of the board agent API. Required when enabled. |
| `BOARD_AGENT_API_TOKEN` | `""` | string | no | Authentication token for the board agent API. Required when enabled. Masked in logs and `repr`. |
| `BOARD_AGENT_REPO_ID` | `""` | string | no | Board repository identifier. Required when enabled. |
| `BOARD_AGENT_WRITE_OPS` | `true` | boolean | no | Whether write operations are allowed. Set to `false` for a read-only agent. Accepts `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off`. |

---

## Multi-account variables

When any `MAIL_ACCOUNTS_*` environment variable is present, the loader
switches into **multi-account mode**. Every per-account field from the
sections above is namespaced: `MAIL_<FIELD>` becomes
`MAIL_ACCOUNTS_<n>_<FIELD>` where `<n>` is a zero-based integer.

Global variables (`LLM_API_KEY`, `LLM_PROVIDER_MODEL`, `LANGFUSE_*`,
`LOG_LEVEL`, `LOG_FORMAT`, `LOG_FILE_DIR`, `BOARD_AGENT_*`) are **not**
namespaced — they remain at their bare names above and apply to every
account.

### Per-account identifiers

| Variable | Default | Kind | Required | Description |
|---|---|---|---|---|
| `MAIL_ACCOUNTS_<n>_ID` | *(none)* | string | yes | Stable, filesystem/URL-safe identifier for this account (e.g. `personal`). Must match `^[A-Za-z0-9._-]+$`. |
| `MAIL_ACCOUNTS_<n>_LABEL` | *(none)* | string | no | Human-friendly display name (e.g. `Personal Gmail`). |

### Default account

| Variable | Default | Kind | Required | Description |
|---|---|---|---|---|
| `MAIL_ACCOUNTS_DEFAULT` | *(first index)* | string | no | The `account_id` of the default account. When absent, index `0` is the default. |

### Rules

- Indices must be **contiguous** starting at 0 (`0`, `1`, `2`, …). A gap
  (e.g. `0` and `2` set but `1` missing) raises `ConfigurationError`.
- `MAIL_ACCOUNTS_<n>_ID` is required for every account.
- When `MAIL_ACCOUNTS_<n>_DB_PATH` is omitted, it defaults to
  `.data/<id>/mail.db` (unique per account).
- The presence of **any** `MAIL_ACCOUNTS_*` variable switches the loader
  into multi-account mode; with no such vars set, the loader stays in
  single-account mode (full backward compatibility).

### Example

```sh
# Global (application-wide — NOT namespaced)
LLM_API_KEY=sk-or-v1-abc
LANGFUSE_PUBLIC_KEY=pk-lf-xyz
LANGFUSE_SECRET_KEY=sk-lf-xyz
LOG_LEVEL=DEBUG

# Default account
MAIL_ACCOUNTS_DEFAULT=personal

# Account 0
MAIL_ACCOUNTS_0_ID=personal
MAIL_ACCOUNTS_0_LABEL=Personal Gmail
MAIL_ACCOUNTS_0_IMAP_HOST=imap.gmail.com
MAIL_ACCOUNTS_0_SMTP_HOST=smtp.gmail.com
MAIL_ACCOUNTS_0_USERNAME=me@gmail.com
MAIL_ACCOUNTS_0_PASSWORD=app-password

# Account 1
MAIL_ACCOUNTS_1_ID=work
MAIL_ACCOUNTS_1_LABEL=Work mailbox
MAIL_ACCOUNTS_1_IMAP_HOST=imap.work.example.com
MAIL_ACCOUNTS_1_SMTP_HOST=smtp.work.example.com
MAIL_ACCOUNTS_1_USERNAME=me@work.example.com
MAIL_ACCOUNTS_1_PASSWORD=s3cret
```

---

## Related pages

- [Connecting](connecting.md) — guided setup, YAML config structure, and the
  `detect` auto-configuration command.
- [Deployment](deployment.md) — Docker Compose setup, container build, and
  production operations.
