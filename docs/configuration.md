# Configuration Reference

`robotsix-auto-mail` is configured through **built-in defaults overlaid by a
single YAML config file**. Each field the YAML file supplies overrides its
built-in default; any field you omit keeps its default.

> **Configuration comes from the config file alone.** One environment
> variable is consulted, and it carries no configuration of its own:
>
> - `ROBOTSIX_CONFIG_FILE` — locates the config file (default
>   `config/config.json`).

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
optional application-wide `openrouter:`, `langfuse:`, and `logging:` sections
and an optional `default_account:` key.

```yaml
# Application-wide (top-level) sections
openrouter:
  keys:
    robotsix-auto-mail: sk-or-v1-…
langfuse:
  host: ""
  projects: {}
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
- **Application-wide sections** — `openrouter`, `langfuse`, `models`,
  `logging`, and the per-application level fields — are top-level and apply
  to every account.
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

### `openrouter` and `langfuse` — LLM credentials

The provider key and the tracing credentials are **component-wide**: a mailbox
is not an LLM function, so they are not per-account settings. Their shape is
fixed by the [robotsix component
standard](https://github.com/damien-robotsix/robotsix-standards) so that the
deployment engine can enumerate every component's credentials the same way and
serve them to the fleet consumers that need them (the chat agent's trace proxy,
cost-monitor's reconciliation). Credentials kept in any other shape are
invisible to that discovery — the component's own tracing still works, which is
what makes the breakage easy to miss.

```json
{
  "langfuse": {
    "host": "https://langfuse.example.net",
    "projects": {
      "robotsix-auto-mail": {
        "public_key": "pk-lf-…",
        "secret_key": "sk-lf-…",
        "project_id": ""
      }
    }
  },
  "openrouter": {
    "keys": {
      "robotsix-auto-mail": "sk-or-…"
    }
  },
  "models": {
    "level1": "",
    "level2": "",
    "level3": "",
    "level4": ""
  },
  "triage_level": 1,
  "classifier_level": 1,
  "rules_level": 1,
  "detector_level": 1,
  "draft_level": 1
}
```

Both maps are keyed by **alias**, which is the Langfuse project name. auto-mail
has one LLM function — detection, triage, archiving and draft generation all
run on one key and trace to one project — so it declares one alias,
`robotsix-auto-mail`. The two maps share the alias on purpose: reconciliation
compares what the provider billed for a function against what Langfuse traced
for it, and the shared alias is what makes the two joinable. A key must
therefore fund exactly one function.

| Key | Default | Kind | Required | Description |
|---|---|---|---|---|
| `langfuse.host` | `""` | string | no | Langfuse instance base URL. When empty, the `robotsix-llmio` default (`https://cloud.langfuse.com`) is used. |
| `langfuse.projects` | `{}` | map | no | Alias → `{public_key, secret_key, project_id}`. Tracing is enabled once both keys of an alias are set; `project_id` is optional and only used by consumers that address a project by id. Secret keys are masked in logs and `repr`. |
| `openrouter.keys` | `{}` | map | no | Alias → OpenRouter API key. Get one at <https://openrouter.ai/keys>. Masked in logs and `repr`. |
| `models.level1` | `""` | string | no | Per-level model override for tier 1 (used by triage, classifier, rules, and detector by default). Holds a provider-model identifier (e.g. ``"openrouter[deepseek]-deepseek/deepseek-v4-flash"``). Empty means use the llmio tier-1 default. |
| `models.level2` | `""` | string | no | Per-level model override for tier 2. |
| `models.level3` | `""` | string | no | Per-level model override for tier 3 (used by the draft agent by default). |
| `models.level4` | `""` | string | no | Per-level model override for tier 4. Wired through even though llmio does not yet define a `LEVEL4_DEFAULT`. |
| `triage_level` | `1` | integer | no | Tier level assigned to the inbox triage agent. |
| `classifier_level` | `1` | integer | no | Tier level assigned to the message classifier. |
| `rules_level` | `1` | integer | no | Tier level assigned to the rules-maintenance agent. |
| `detector_level` | `1` | integer | no | Tier level assigned to the account-type detector. |
| `draft_level` | `1` | integer | no | Tier level assigned to the draft-generation agent. |

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

## Runtime config API

The board server exposes the fleet's **standard config surface** over the one
``config/config.json`` it already loads, per robotsix-standards
[config-ownership](https://github.com/damien-robotsix/robotsix-standards/blob/main/docs/config-ownership.md).
Runtime configuration is fully self-owned by the component: no central-deploy
call is required to change a setting.

Secrets are **typed** — every field declared ``SecretStr`` on ``MailConfig`` —
so masking is driven by the model, never guessed from a field's name.

### GET /config

Returns the effective config with every secret masked, the JSON Schema the UI
renders from, and the current version.

```json
{
  "config": {
    "accounts": [
      {
        "account_id": "personal",
        "config": {"imap_host": "imap.gmail.com", "password": "**********"}
      }
    ],
    "default_account_id": "personal"
  },
  "schema": {"...": "the committed config/config.schema.json"},
  "version": 7
}
```

### PUT /config

Accepts a partial update — only the keys being changed. Omitted keys keep
their stored value. A secret submitted blank, or as the ``**********`` mask,
is treated as unchanged; only an explicitly typed secret overwrites the
stored one. Accounts are matched by ``account_id``, so a secret stays with
its own account even if the list is reordered.

The merged result is validated against ``MailAccountsConfig`` before anything
is written — a rejected update leaves the stored config untouched and answers
with the fleet's error envelope:

```json
{
  "type": "urn:robotsix:error:config-validation",
  "title": "Config validation failed",
  "detail": "accounts.0.config.imap_tls_mode: ...",
  "instance": "/config"
}
```

### GET /config/versions and POST /config/rollback

Every write is versioned in ``config_versions.json`` beside the config file
(the last 20). An entry records which dotted keys changed — a secret change
is recorded as ``"<key> (secret)"`` — and the snapshot itself **never stores
a secret value**. Rolling back therefore restores non-secret values and
leaves the live secrets in place.

### The Settings page

``/settings-panel``, linked from the board header, mounts the fleet's shared
config panel from
[`@robotsix/ui`](https://github.com/damien-robotsix/robotsix-ui) against that
surface. Auto-mail ships **no settings form of its own** — that is what keeps
this page, the deploy UI, and every other fleet UI presenting identical
fields with identical semantics.

The panel's build artifacts are vendored into the image (the ``ui`` stage in
the Dockerfile); for a local checkout run ``scripts/vendor-ui.sh``.

Account *creation* remains a separate flow (``/add-account``), because it
validates a mailbox connection rather than just writing config;
``POST /delete-account`` likewise stays as a one-click affordance.

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
