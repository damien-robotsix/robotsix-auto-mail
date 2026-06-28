# Component Agent

The component agent is an optional, opt-in **agent-comm responder** that
registers on the shared `robotsix_agent_comm` broker under agent-id
`board-manager-robotsix-auto-mail`.  When enabled, it serves three typed
request kinds — **monitor** (live telemetry), **config-get** (redacted
snapshot), and **config-set** (validate-then-apply with audit) — allowing
other agents to observe and reconfigure the running `robotsix-auto-mail`
instance without a restart.

## Enabling the component agent

The component agent is **disabled by default**.  Enable it in one of two ways:

**YAML config file** (`config/mail.local.yaml`):

```yaml
component_agent:
  enabled: true
  agent_id: board-manager-robotsix-auto-mail
  broker_host: "broker.example.com"
  broker_port: 443
  broker_token: "your-agent-token"
  # broker_tls_ca: ""   # optional — path to custom CA cert PEM
```

**Environment variables:**

```sh
export COMPONENT_AGENT_ENABLED=true
export COMPONENT_AGENT_BROKER_HOST=broker.example.com
export COMPONENT_AGENT_BROKER_PORT=443
export COMPONENT_AGENT_BROKER_TOKEN=your-agent-token
# export COMPONENT_AGENT_BROKER_TLS_CA=/path/to/ca.pem
# export COMPONENT_AGENT_ID=board-manager-robotsix-auto-mail
```

> **Multi-account note:** `component_agent:` is a **top-level** section
> (application-wide), not per-account.  In a multi-account YAML file it
> lives alongside `llm:`, `langfuse:`, and `board_agent:`, outside the
> `accounts:` list.  Per-account `component_agent:` blocks are rejected
> with an error.

## Required configuration

| Field | Env var | Required | Purpose |
|---|---|---|---|
| `component_agent.enabled` | `COMPONENT_AGENT_ENABLED` | no *(default `false`)* | Enable the component-agent responder |
| `component_agent.agent_id` | `COMPONENT_AGENT_ID` | no *(default `board-manager-robotsix-auto-mail`)* | Agent identifier on the broker |
| `component_agent.broker_host` | `COMPONENT_AGENT_BROKER_HOST` | yes *(when enabled)* | Broker server hostname |
| `component_agent.broker_port` | `COMPONENT_AGENT_BROKER_PORT` | no *(default `443`)* | Broker server port |
| `component_agent.broker_token` | `COMPONENT_AGENT_BROKER_TOKEN` | yes *(when enabled)* | Agent authentication token for the broker (redacted in logs/repr) |
| `component_agent.broker_tls_ca` | `COMPONENT_AGENT_BROKER_TLS_CA` | no | Path to a custom CA certificate PEM (optional; uses system trust store when empty) |

## Token-required-when-enabled invariant

The `component_agent_broker_token` and `component_agent_broker_host` fields
are **required** when `component_agent_enabled` is `true`.  Starting the
server with `enabled: true` but an empty token or host raises a
`ConfigurationError` at startup — the invariant is checked in both
`MailConfig.__post_init__` (defense in depth) and
`ComponentAgentSettings.__post_init__`.

## Request kinds

### `monitor`

Returns genuine live telemetry from the running process:

- **DB reachability** and counts (total records, untriaged records).
- **Watermark states**: `imap_uid`, `reconcile:state`, `triage_run:state`,
  `batch_op:state`.
- **Config summary**: archive/triage/calendar/component-agent enable flags.
- **Capabilities** list: `["monitor", "config-get", "config-set"]`.

### `config-get`

Returns a **flat dotted-path snapshot** of every `MailConfig` field, with
every secret field (`password`, `llm_api_key`, `oauth2_token`, etc.)
replaced by the redaction sentinel `"<redacted>"`.  Also returns a
`describe` map with current (redacted) value, type/kind, and whether each
key is runtime-settable.

### `config-set`

Validates a `{"updates": {…}}` map **before** applying:

1. Rejects unknown keys and non-settable keys with
   `code="invalid_key"`.
2. Coerces/validates each value per the field's declared `kind`.
3. Builds a candidate `MailConfig` via `dataclasses.replace` so model
   invariants run — on failure returns `code="invalid_value"`.
4. On success, swaps the running config atomically and emits an audit
   log line with secret values redacted.

Only **runtime-toggleable** fields are settable.  Startup-only fields
(IMAP/SMTP host/credentials, `store.path`, broker-connection fields,
`account_id`) are excluded — they can only be changed by restarting the
server.

## Security / redaction behavior

- Every secret field in snapshots is replaced by `"<redacted>"`.
- Every `config-set` audit log line redacts secret old/new values.
- The `component_agent_broker_token` is redacted in `repr(MailConfig)`.
- Authentication uses the existing broker bearer-token scheme — no new
  side channel is introduced.

## Installation

The component agent requires the `robotsix-agent-comm` Python package, which
is already declared as an **optional extra** in `pyproject.toml` under the
`calendar` extra:

```toml
[project.optional-dependencies]
calendar = ["robotsix-agent-comm"]
```

Install the extra with:

```sh
uv sync --extra calendar
# or, if you're using pip:
pip install "robotsix-auto-mail[calendar]"
```

When the dependency is **not** installed and the agent is enabled, a log
line is emitted and the server starts normally without the agent — the
guarded import ensures `robotsix-auto-mail` never hard-crashes on a
missing optional dependency.

## Verifying the agent is running

When the component agent starts, it registers via the broker's
`BrokeredRegistry` and its `on_request` handler begins servicing
requests.  Other agents on the broker can discover it by querying the
registry for agent-id `board-manager-robotsix-auto-mail`.

In the `serve` command's output you should see the standard
`Serving board on …` message; the agent runs in a background daemon
thread and its lifecycle is managed automatically:

- **Start:** after the board-agent start block, before the HTTP
  server loop.
- **Stop:** in the `finally` block, so it shuts down on normal exit,
  `KeyboardInterrupt`, or port-in-use errors.

## How it works

1. `robotsix-auto-mail serve` starts.
2. If `find_spec("robotsix_agent_comm")` is not `None` **and**
   `component_agent_enabled` is `true`, `start_component_responder()` is called.
3. A `ComponentAgentResponder` is created with a mutable config holder.
4. A `BrokeredRegistry` + `NetworkedBrokerTransport` is built from the
   broker connection fields, using TLS + bearer-token auth.
5. An `Agent` from `robotsix_agent_comm.sdk` is created in pull (mailbox)
   mode and its `on_request` handler is wired to the responder.
6. A background daemon thread calls `agent.start()`, which registers the
   agent's mailbox endpoint on the broker and begins long-polling for
   requests.
7. On shutdown, `stop_component_responder()` signals a stop event and
   joins the thread with a 5-second timeout.
