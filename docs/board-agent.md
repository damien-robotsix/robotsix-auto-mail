# Board Agent (optional)

`robotsix-auto-mail` can optionally run a **board agent** — an
`agent-comm` agent that exposes the mill board's ticket lifecycle over
agent-comm messages, so other agents (e.g. a mail triage agent) can query
and drive the board programmatically.

The board agent is **disabled by default**.  Enable it per deployment
through configuration.

---

## Enabling the board agent

Set the environment variable `BOARD_AGENT_ENABLED=true` (or the YAML key
`board_agent.enabled: true`) and provide the board API connection details:

### Required settings

| Variable | YAML path | Description |
|---|---|---|
| `BOARD_AGENT_ENABLED` | `board_agent.enabled` | Set to `true` to enable (default: `false`). |
| `BOARD_AGENT_API_URL` | `board_agent.api_url` | Base URL of the board REST API. |
| `BOARD_AGENT_API_TOKEN` | `board_agent.api_token` | API token for authenticating to the board. |
| `BOARD_AGENT_REPO_ID` | `board_agent.repo_id` | Repository id to scope board operations to. |

### Optional settings

| Variable | YAML path | Default | Description |
|---|---|---|---|
| `BOARD_AGENT_WRITE_OPS` | `board_agent.write_ops` | `true` | Set to `false` for a read-only agent (write ops return an error). |

---

## Example: `.env`

```sh
BOARD_AGENT_ENABLED=true
BOARD_AGENT_API_URL=https://board.example.com/api
BOARD_AGENT_API_TOKEN=your-api-token
BOARD_AGENT_REPO_ID=my-repo
BOARD_AGENT_WRITE_OPS=true
```

## Example: `config/mail.local.yaml`

```yaml
board_agent:
  enabled: true
  api_url: https://board.example.com/api
  api_token: your-api-token
  repo_id: my-repo
  write_ops: true
```

---

## How it works

When `board_agent_enabled` is `true` and `robotsix-board-agent` is
installed, the `serve` command starts a `BoardAgent` that:

1. Creates an agent-comm `Registry`.
2. Constructs a `BoardAgent` pointed at the configured board API.
3. Registers the agent and begins listening for structured operation
   requests over agent-comm.

When the server shuts down (e.g. `Ctrl-C`), the agent is stopped cleanly.

If `robotsix-board-agent` is not installed (the package is an optional git
dependency), a warning is logged and the server starts without the agent.

---

## Operations

The board agent accepts structured operation requests of the form
`{"op": "<name>", "args": {...}}`.  See the `robotsix-board-agent`
documentation for the full operation reference.

---

## Security

- The board API token (`board_agent_api_token`) is masked in logs and
  `repr()` output.
- Write operations can be disabled per deployment by setting
  `board_agent_write_ops=false` — the agent will return a clear error
  for any write op.
- The agent is opt-in and off by default, so existing deployments are
  unaffected.
