# Board Agent

The board agent is an optional, opt-in **agent-comm bridge** that exposes the
mill board's full ticket lifecycle over agent-comm messages. When enabled,
other agents can drive the board programmatically — querying, filing,
commenting, transitioning, approving, merging, resuming, and migrating
tickets — instead of via the HTTP API or a human.

## Enabling the board agent

The board agent is **disabled by default**.  Enable it in one of two ways:

**YAML config file** (`config/mail.local.yaml`):

```yaml
board_agent:
  enabled: true
  api_url: "https://your-board-api.example.com"
  api_token: "your-api-token"
  repo_id: "your-repo-id"
  # write_ops: true   # default — set to false for read-only
```

**Environment variables:**

```sh
export BOARD_AGENT_ENABLED=true
export BOARD_AGENT_API_URL=https://your-board-api.example.com
export BOARD_AGENT_API_TOKEN=your-api-token
export BOARD_AGENT_REPO_ID=your-repo-id
# export BOARD_AGENT_WRITE_OPS=true  # default
```

> **Multi-account note:** `board_agent:` is a **top-level** section
> (application-wide), not per-account.  In a multi-account YAML file it
> lives alongside `llm:` and `langfuse:`, outside the `accounts:` list.
> Per-account `board_agent:` blocks are rejected with an error.

## Required configuration

| Field | Env var | Required | Purpose |
|---|---|---|---|
| `board_agent.enabled` | `BOARD_AGENT_ENABLED` | no *(default `false`)* | Enable the board agent |
| `board_agent.api_url` | `BOARD_AGENT_API_URL` | yes *(when enabled)* | Board agent API base URL |
| `board_agent.api_token` | `BOARD_AGENT_API_TOKEN` | yes *(when enabled)* | Authentication token for the board agent API |
| `board_agent.repo_id` | `BOARD_AGENT_REPO_ID` | yes *(when enabled)* | Board repository identifier |
| `board_agent.write_ops` | `BOARD_AGENT_WRITE_OPS` | no *(default `true`)* | Allow write operations; set to `false` for a read-only agent |

## The `write_ops` gate

The `write_ops` gate (default `true`) controls whether the board agent
accepts write requests.  When set to `false`, the agent only services
**read** requests — querying tickets, listing comments, etc. — and
write operations (file, comment, transition, approve, merge, resume,
migrate) are blocked.

This is useful for:

- **Audit / monitoring deployments** where you want an agent to observe
  the board without modifying it.
- **Staging environments** where you want to test agent-comm routing
  without risk of side effects.
- **Gradual rollout** — start read-only, verify connectivity, then
  enable writes.

## Verifying the agent is running

When the board agent starts, it registers itself via the agent-comm
`Registry`.  Check the agent-comm registry logs to confirm registration
was successful.  In the `serve` command's output you should see the
standard `Serving board on …` message; the agent runs in a background
daemon thread and its lifecycle is managed automatically:

- **Start:** after the stale-triage-state cleanup, before the HTTP
  server loop.
- **Stop:** in a `finally` block, so it shuts down on normal exit,
  `KeyboardInterrupt`, or port-in-use errors.

## Installation

The board agent requires the `robotsix-board-agent` Python package, which
is declared as an **optional extra** in `pyproject.toml`:

```toml
[project.optional-dependencies]
board-agent = ["robotsix-board-agent"]

[tool.uv.sources]
robotsix-board-agent = { git = "https://github.com/damien-robotsix/robotsix-board-agent.git", rev = "main" }
```

Install the extra with:

```sh
uv sync --extra board-agent
# or, if you're using pip:
pip install "robotsix-auto-mail[board-agent]"
```

When the dependency is **not** installed and the agent is enabled, a
warning is logged to stderr and the server starts normally without the
agent — the guarded import ensures `robotsix-auto-mail` never
hard-crashes on a missing optional dependency.

## How it works

1. `robotsix-auto-mail serve` starts.
2. If `board_agent.enabled` is `true`, `start_board_agent()` is called.
3. A background daemon thread is spawned with its own `asyncio` event loop.
4. The `BoardAgent` connects to the configured board API, registers via
   agent-comm, and begins servicing agent-comm requests.
5. On shutdown (`KeyboardInterrupt`, `OSError`, or normal exit), a
   `finally` block calls `stop_board_agent()`, which signals the stop
   event and joins the thread with a 5-second timeout.
