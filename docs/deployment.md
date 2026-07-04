# Deployment Guide

How to build, run, and maintain the `robotsix-auto-mail` container — from
first checkout to production push.

`robotsix-auto-mail` is a **CLI tool** with an optional long-running
**web board daemon**. Most operations (`probe`, `ingest`, `board`) are
one-shot CLI invocations via `docker compose run`. The web kanban board
is a persistent HTTP daemon started via `docker compose up board`.
This guide covers both patterns.

---

## Prerequisites

| What | Minimum | Check with |
|---|---|---|
| Docker Engine | 20.10+ | `docker --version` |
| Docker Compose | Compose plugin 2.0+ | `docker compose version` |
| Git | any recent | `git --version` |


Installation guides (do **not** reproduce here):
- [Docker Engine install](https://docs.docker.com/engine/install/)
- [Docker Compose install](https://docs.docker.com/compose/install/)

---

## First-time setup

### 1. Clone the repository

```sh
git clone https://github.com/your-org/robotsix-auto-mail.git
cd robotsix-auto-mail
```

### 2. Create your local configuration

The recommended path is a single YAML config file:

```sh
cp docs/config/mail.local.example.yaml config/mail.local.yaml
```

Then edit `config/mail.local.yaml` with your real IMAP and SMTP credentials:

```sh
$EDITOR config/mail.local.yaml
```

### 2a. Alternative: auto-detect provider settings (detect)

Instead of manually creating the whole `config/mail.local.yaml`, you can
auto-generate the account settings from just your email address. First put
your LLM API key in the config file's top-level `llm:` section (e.g. copy the
example and set `llm.api_key`), then run:

```sh
docker compose run robotsix-auto-mail detect user@gmail.com
```

This calls an LLM to look up the correct IMAP/SMTP settings and writes
`config/mail.local.yaml`, prompting for your password and including it in
that file.  See [docs/connecting.md](connecting.md#auto-detection-with-detect)
for full details.

The file `config/mail.local.yaml` is **git-ignored** (`config/mail.local.yaml`
in `.gitignore`), so your credentials stay local and never land in the repo.

---

## Build

```sh
docker compose build
```

The [`Dockerfile`](../Dockerfile) has two stages:

| Stage | What it does |
|---|---|
| `builder` | Installs the Python package (wheel) from `pyproject.toml` |
| `production` | Copies **only** the installed artifacts from `builder`, creates a non-root `app` user (UID 1000), and sets the entrypoint |

The final image runs as `app` (UID 1000).  The image ships an HTTP
healthcheck (`GET :8080/health`) that both Compose files rely on for the
long-running web server — the `board` service maps a port for it, while the
one-shot ingester disables the healthcheck since it runs no HTTP server.

To build without the Compose cache:

```sh
docker compose build --no-cache
```

---

## Run locally

CLI operations (`probe`, `ingest`, `board`) use `docker compose run` — they are
one-shot commands.  The web board is a long-running daemon started with
`docker compose up board`; see [Start the web board](#start-the-web-board).

### Probe connectivity (always run first)

```sh
docker compose run robotsix-auto-mail probe
```

This opens an IMAP and SMTP connection, prints server diagnostics, and exits
with code `0` when both succeed.  No email is read or sent — it is a read-only
sanity check.  See [docs/connecting.md](connecting.md#the-probe-command) for
sample output.

### Ingest mail

```sh
docker compose run robotsix-auto-mail ingest
```

Fetches new messages from the configured IMAP inbox and stores them in the
local SQLite database.  See [docs/ingestion.md](ingestion.md) for the full
pipeline.

### View the inbox

```sh
docker compose run robotsix-auto-mail board
```

Prints a read-only view of stored messages.  Requires a prior `ingest` run.
See [docs/connecting.md](connecting.md#the-board-command) for output format.

### Start the web board

```sh
docker compose up board
# → http://localhost:${BOARD_PORT:-8080}/board
```

The board service runs as a long-lived daemon (restart policy:
`unless-stopped`).  Inside the container it always serves on **8080** (so the
image healthcheck `GET :8080/health` passes); `BOARD_PORT` remaps only the
host-side port (default: **8080**).  Open the URL in a browser to see the
four-column kanban board with per-card Move dropdowns.  Press `Ctrl-C` to stop
the daemon.

**Note:** set `BOARD_PORT` in your shell or `.env` file to publish on a
different host port: `BOARD_PORT=9090 docker compose up board` maps host 9090
→ container 8080.

### Ephemeral containers, persistent data

Each `docker compose run` creates a **new, ephemeral** container that is
removed when the command exits.  The SQLite database lives outside the
container in `./.mail_data` on the host (a git-ignored bind-mount), so it
persists across runs and container lifecycles.

To inspect it:

```sh
ls -la .mail_data/        # mail.db lives here
```

---

## Configuration quick-reference

Configuration is loaded from a single YAML config file; any field you omit
falls back to its built-in default.

| Path | Mechanism | How to use |
|---|---|---|
| **YAML file** | A single `config/mail.local.yaml` | Copy `docs/config/mail.local.example.yaml` → `config/mail.local.yaml` and edit. Located via `ROBOTSIX_CONFIG_FILE`. |

Full config-key details are documented in
**[docs/connecting.md](connecting.md)**.  Do not duplicate that reference
here — the connecting doc is authoritative.

### How configuration reaches the container

- `docker-compose.yml` sets `ROBOTSIX_CONFIG_FILE=/home/app/config/config.json`.
- The `./config:/home/app/config` bind-mount maps the host `config/`
  directory into the container.
- Editing `config/mail.local.yaml` on the host takes effect on the **next**
  `docker compose run` — no rebuild required.

---

## `docker-compose.yml` structure

The Compose file defines two services that share the same image and data:
`robotsix-auto-mail` (the periodic ingester) and `board` (the web board).

### `services.robotsix-auto-mail`

| Key | Value | Why |
|---|---|---|
| `build.context` | `.` | Build from the repo root. |
| `build.dockerfile` | `Dockerfile` | The multi-stage Dockerfile. |
| `command` | `ingest --watch` | Default: run the periodic ingester. Overridden by `docker compose run … <cmd>` for one-shot commands. |
| `stdin_open` | `true` | Keeps stdin open so one-shot interactive commands (e.g. `detect`'s password prompt) work. |
| `tty` | `false` | No pseudo-TTY allocation; output is plain streams. |
| `restart` | `unless-stopped` | The default command is a long-running daemon, so it should stay up. |
| `environment` | `ROBOTSIX_CONFIG_FILE` | Points the tool at the mounted config file; all credentials (including LLM keys) live in that file. |
| `volumes` | Three entries (see below) | Config bind-mount + data bind-mount + log bind-mount. |

`docker compose up -d` runs this service (the ingester) alongside `board`.
A one-shot command overrides `command:` at runtime — e.g.
`docker compose run robotsix-auto-mail probe`.

### Volumes

| Volume | Type | Purpose |
|---|---|---|
| `./config:/home/app/config` | Bind-mount | Makes host config files available inside the container without a build. |
| `./.mail_data:/data` | Bind-mount | Persists the SQLite database in the project dir (git-ignored), mounted at `/data` inside the container. |

### `services.board`

The `board` service runs the same image but starts the web server:

| Key | Value | Why |
|---|---|---|
| `command` | `serve --port 8080` | Starts the web server as a daemon on the fixed container port 8080. |
| `restart` | `unless-stopped` | Restarts if the process crashes. |
| `ports` | `"${BOARD_PORT:-8080}:8080"` | Maps host `BOARD_PORT` (default 8080) to the container's 8080. |
| `environment` | `ROBOTSIX_CONFIG_FILE: /home/app/config/config.json` | Same config as the ingester. |
| `volumes` | Same as the ingester | Shares `./.mail_data` so the ingester and board see the same database. |

There is no `stdin_open` or `tty` — the board is a daemon, not an
interactive process.

---

## Production deployment

### Pull the published image

On every `v*` git tag, the [`release.yml`](../.github/workflows/release.yml)
workflow delegates to the shared
[`docker-release.yml`](https://github.com/damien-robotsix/robotsix-github-workflows/blob/main/.github/workflows/docker-release.yml)
reusable workflow from `robotsix-github-workflows`, which builds the `Dockerfile` and
publishes a semver-tagged image to the GitHub Container Registry complete with
SLSA build provenance and an SBOM attestation. A separate `trivy` job then
scans the published image and uploads SARIF results to GitHub Code Scanning.
Instead of building locally you can pull a versioned image directly:

```sh
docker pull ghcr.io/damien-robotsix/robotsix-auto-mail:1.0.0
```

Tags produced by the reusable workflow:
- **Tag push** (`v1.0.0`): `1.0.0`, `sha-<short>`
- **Branch push** (`main`): `main`, `latest`, `sha-<short>`

### Build the production image

The same `Dockerfile` that works for local development also targets
production — its final stage is already a slim, non-root production image:

```sh
docker compose build
```

For a versioned, registry-ready build:

```sh
docker build -t registry.example.com/robotsix-auto-mail:v1.0.0 .
```

### Tag and push

```sh
docker tag robotsix-auto-mail:latest registry.example.com/robotsix-auto-mail:v1.0.0
docker push registry.example.com/robotsix-auto-mail:v1.0.0
```

### Continuous deployment (deploy.robotsix.net)

The always-on production deployment is operated by the external
**central-deploy** system and reachable at `https://deploy.robotsix.net/mail`.
The [`deploy/`](../deploy/) directory holds the central-deploy **contract** —
[`deploy/docker-compose.yml`](../deploy/docker-compose.yml), marked
`central-deploy-contract-version: 1`. central-deploy consumes it to **pull**
the published image and run the stack, rather than building from source. The
orchestrator itself lives outside this repo, so server provisioning, TLS, and
ingress are not configured here.

- Pushing to `main` publishes a moving `ghcr.io/.../robotsix-auto-mail:main`
  image (the reusable `docker-release.yml` workflow tags both `main` pushes
  and `v*` tag pushes). central-deploy pulls `:main` to pick up new builds.
- The contract runs two services — `ingester` (the sole datastore writer)
  and `board` (the read-only web board, published on `8080`) — both from the
  same image, sharing the named volumes `auto-mail-config`, `auto-mail-data`,
  and `auto-mail-logs`.
- The `ingester` runs `ingest --watch --heartbeat-file
  /data/ingest.heartbeat`. The `--heartbeat-file` CLI flag
  makes each watch pass touch that file, and the service's healthcheck is a
  small Python probe that fails if the heartbeat is missing or older than 30
  minutes — so a wedged ingester is detected even though it serves no HTTP.
- TLS termination and HTTP basic auth are handled by the **central-deploy
  gateway** in front of the board; the board itself has no authentication.
- Configuration is **not** managed by central-deploy. The app reads the JSON
  file at `ROBOTSIX_CONFIG_FILE` (`/home/app/config/config.json`), seeded
  manually into the `auto-mail-config` volume — see
  [`config/config.example.json`](../config/config.example.json) for the shape,
  or generate one with `robotsix-auto-mail detect`.

#### `robotsix.deploy.*` labels

The `board` service carries labels that tell central-deploy how to run and
wire the stack:

| Label | Meaning |
|---|---|
| `robotsix.deploy.primary: "true"` | Marks `board` as the primary/ingress service the gateway routes to. |

There are deliberately **no** `config-target` / `config-assist` labels:
central-deploy's config writer only produces YAML, while the app reads JSON,
so central-deploy config management is opted out of entirely.

#### Day-2 operations

Run on the central-deploy host, against the contract compose project:

| Task | Command |
|---|---|
| Tail board logs | `docker compose logs -f board` |
| Tail ingester logs | `docker compose logs -f ingester` |
| Force an image update | `docker compose pull && docker compose up -d` |
| One-shot CLI command | `docker compose run --rm ingester <cmd>` (probe, triage, …) |
| Restart the board only | `docker compose restart board` |
| Stop everything | `docker compose down` |

The database on the `auto-mail-data` volume is shared by both services; the
ingester is the sole writer and the board only reads it, so there is no
concurrent-writer contention.

### Run on a production host

The same `docker compose run` pattern works — just make sure `config/` is
populated with the production credentials and the image is pulled:

```sh
# On the production host, with config/mail.local.yaml in place:
docker compose run robotsix-auto-mail probe
docker compose run robotsix-auto-mail ingest
```

If you are not using Compose on the production host, replicate the setup
with a plain `docker run`:

```sh
docker run --rm \
  -v "$(pwd)/config:/home/app/config" \
  -v "$(pwd)/.mail_data:/data" \
  -e ROBOTSIX_CONFIG_FILE=/home/app/config/config.json \
  registry.example.com/robotsix-auto-mail:v1.0.0 \
  probe
```

### What the entrypoint does

Before the Python interpreter starts, [`entrypoint.sh`](../entrypoint.sh)
validates that a readable YAML config file exists at
`${ROBOTSIX_CONFIG_FILE:-config/config.json}`.

If the file is missing or unreadable, the script prints a clear error message
to `stderr` (naming the config file and the `detect` command) and exits with
code `1`.  This means config failures surface immediately — no Python
traceback, no mysterious `KeyError` deep in the config loader.

The entrypoint also supports optional `envsubst` templating: if `envsubst`
is available in the image and a config file is in use, the file is run
through `envsubst` before the Python CLI sees it.  If `envsubst` is not
present (it usually isn't in the slim image), the raw config file is used
as-is — this is not an error.

---

## Updating a deployment

1.  **Pull the latest code:**

    ```sh
    git pull
    ```

2.  **Rebuild the image:**

    ```sh
    docker compose build
    ```

3.  **Run as normal — the next invocation picks up the new image:**

    ```sh
    docker compose run robotsix-auto-mail ingest
    ```

Because CLI invocations are one-shot, there is no zero-downtime concern
for `probe`, `ingest`, or `board`.  Each `docker compose run` creates a
fresh container from the current image.  If the web board daemon is
running (`docker compose up board`), restart it after a rebuild:
`docker compose up -d board` (or `docker compose restart board`).

### Full reset (including database)

If you want to wipe the SQLite database and start fresh:

```sh
docker compose down
rm -rf ./.mail_data
```

Because the database is a host bind-mount (not a named volume), removing the
`./.mail_data` directory is what clears it — `docker compose down -v` will
not.  The next `ingest` re-creates the database from scratch and fetches all
messages from the watermark baseline.

---

## Troubleshooting / FAQ

### "Missing configuration file"

```text
Missing configuration file: config/mail.local.yaml
```

The entrypoint validated config before launching Python and could not read a
config file at `${ROBOTSIX_CONFIG_FILE:-config/config.json}`.

**Diagnose:**

```sh
# Check that the config file exists and has content
cat config/mail.local.yaml

# Check that the bind-mount is working
docker compose run robotsix-auto-mail ls -l /home/app/config/config.json
```

**Fix:**  ensure the config file exists and is readable — copy the example
(`cp docs/config/mail.local.example.yaml config/mail.local.yaml`) or generate
one with `robotsix-auto-mail detect user@example.com`, then verify the
bind-mount isn't being shadowed by another volume definition.

---

### IMAP / SMTP connectivity failures

`robotsix-auto-mail` exposes **no ports** — there is no local port conflict.

If `probe` fails with a connection error, the remote mail server is
unreachable from the container.  Possible causes:

- Firewall or VPN blocking outbound IMAP (993) / SMTP (587).
- Incorrect `imap.host` or `smtp.host` in `config/mail.local.yaml`.

**Diagnose:**

```sh
# Run probe as first step — it gives targeted error messages
docker compose run robotsix-auto-mail probe
```

**Fix:**  verify the hostnames and ports in your config.  Check that the
host running Docker can reach those hosts on the configured ports.

---

### Volume permissions

The container runs as `app` (UID 1000) and writes the database into the
bind-mounted `./.mail_data`.  On the host the files will be owned by UID 1000;
if your host user is not UID 1000 you may need to adjust ownership, and
overriding the user (e.g. `docker compose run --user root`) can leave files
that future runs as `app` cannot read.

**Diagnose:**

```sh
# See what user the container runs as
docker compose run robotsix-auto-mail whoami

# Inspect data ownership
docker compose run robotsix-auto-mail ls -la /data/
ls -la ./.mail_data/      # on the host
```

**Fix:**  do not override `--user` unless you have a specific reason.  If
the data was created under a different UID, reset it:

```sh
docker compose down
rm -rf ./.mail_data
docker compose run robotsix-auto-mail ingest   # re-creates the db as app
```

---

### "envsubst: command not found" (or envsubst is silently skipped)

`entrypoint.sh` uses `envsubst` for optional config-file templating **only
if it is available**.  The slim Python image does not include `gettext`
(which provides `envsubst`), so templating is silently skipped.

This is **not an error** — the raw config file is used as-is.  If you need
`envsubst` (e.g. to inject secrets at runtime), install `gettext` in a
custom image or use a different base.  The entrypoint was designed to
degrade gracefully here.

---

### "database is locked" / SQLite database corruption

If two `ingest` commands run concurrently against the same `./.mail_data`
database, SQLite may return `database is locked`.  The tool does not use
WAL mode by default, so concurrent writers will contend.

**Fix:**  do not run concurrent `ingest` commands.  The tool is designed
for sequential, single-writer access.  If you have scheduled (cron) runs,
ensure the previous run has completed before starting the next:

```sh
# Example cron wrapper — flock prevents overlap
flock -n /tmp/mail-ingest.lock docker compose run robotsix-auto-mail ingest
```

If the database is already corrupted, reset it:

```sh
docker compose down -v
docker compose run robotsix-auto-mail ingest
```

---

## Further reading

- **[docs/connecting.md](connecting.md)** — full config key reference,
  precedence rules, and the `probe`/`board` commands.
- **[docs/configuration.md](configuration.md)** — full configuration
  reference.
- **[docs/ingestion.md](ingestion.md)** — ingestion pipeline, schema,
  idempotency guarantees, and `ingest` CLI usage.
- **[README.md](../README.md)** — project overview, layout, and status.
