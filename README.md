# robotsix-auto-mail

Automated email handling — sending, receiving, and routing email through programmatic interfaces.

## Purpose

`robotsix-auto-mail` is a dedicated module for automated email processing. Once implemented, it will handle tasks like sending, receiving, and routing email programmatically, removing manual email steps from automated workflows.

## Project status

The mail ingestion pipeline is implemented: `robotsix-auto-mail` can fetch messages from an IMAP inbox, parse them into structured records, and store them idempotently in a local SQLite database.  See [docs/ingestion.md](docs/ingestion.md) for the full ingestion model, schema, configuration, and CLI usage.

**Language:** Python 3.14, chosen for its standard-library support for IMAP, SMTP, MIME parsing, and SQLite — the four core capabilities required by the [ROADMAP](ROADMAP.md).

## Directory layout

| Directory | Role |
|---|---|
| `src/robotsix_auto_mail/` | Production Python package, following the `src` layout. |
| `tests/` | Test code mirroring the `src/` package structure. |
| `config/` | Example and sample configuration files for operators. |
| `docs/` | Project documentation, including architecture decision records. |
| Root | Top-level project configuration, build scripts, and this README. |

> **Rule:** Every new repo file must be registered in `docs/modules.yaml`
> under exactly one module's `paths` list; root-level docs (README.md,
> SECURITY.md, ROADMAP.md, AGENT.md) belong to the `core` module.
> Unlisted files are flagged by the module-classification drift check and
> will fail CI.

## Connecting

Configuration keys, precedence rules, and walkthroughs of the `probe`
diagnostics command, the `ingest` mail-fetching command, and the `board`
web view are documented in [docs/connecting.md](docs/connecting.md).

Configuration is loaded from a single JSON config file (default
`config/config.json`, located via `ROBOTSIX_CONFIG_FILE`) using the
multi-account `accounts:` shape — the only supported config-file shape. Copy
`config/config.example.json` to `config/config.json` and fill in
your values, or run `robotsix-auto-mail detect` to generate it from your email
address.

## Further documentation

- [docs/architecture.md](docs/architecture.md) — system design, module
  relationships, and the ingestion data flow.
- [docs/testing.md](docs/testing.md) — how to run the tests, their
  organization, and the local quality gate.
- [docs/troubleshooting.md](docs/troubleshooting.md) — diagnosing
  connection, TLS, and authentication failures.

## Documentation site

Project documentation is published as a browsable site on GitHub Pages (see the [GitHub Pages settings](https://github.com/damien-robotsix/robotsix-auto-mail/settings/pages) to enable it).

To build and serve documentation locally during development:

```sh
uv sync --frozen --extra docs
uv run --frozen mkdocs serve
```

Then open http://localhost:8000 in your browser. The site will auto-reload as you edit markdown files in the `docs/` directory.

## Installation

This package is **not published to PyPI** (`pip install robotsix-auto-mail` will not work).

Supported install paths:

```sh
# From source (requires Python 3.14 and uv)
git clone https://github.com/damien-robotsix/robotsix-auto-mail.git
cd robotsix-auto-mail
uv sync --frozen
```

Or pull the pre-built container image from GHCR:

```sh
docker pull ghcr.io/damien-robotsix/robotsix-auto-mail:latest
```

See [docs/connecting.md](docs/connecting.md) for full configuration instructions.

## Development

This repository uses [pre-commit](https://pre-commit.com) to lint and
format code before each commit.  After cloning, run:

```sh
uvx pre-commit install
```

## Web Board

Start the kanban board to review and triage ingested mail in a browser:

```sh
# Native (port 8080 by default)
robotsix-auto-mail serve

# Docker (host port 8080 by default, configurable via BOARD_PORT)
docker compose up board

# Then open http://localhost:<port>/board
```

The board shows ingested mail in seven columns — Inbox, Human triage, Pending
action, To archive, To delete, To calendar, To answer — with
per-card Move dropdowns and a 30-second auto-refresh. Cards display triage
badges showing the decision action (e.g. answer, archive) with the reason
visible on hover. Click any card to view full details including the triage
action, reason, and confidence.

Composing a reply or a new message is done via the `POST /compose-draft`
endpoint, which builds a genuine RFC822 message — correct sender, recipients,
subject, threading headers, body, and all file-hub attachments — and `APPEND`s
it into the account's IMAP **Drafts** folder. The draft then appears natively in
your own mail client (Gmail, Roundcube, …), where you review and send it
manually. The board itself stores no drafts and sends no mail.
Full details are in [docs/connecting.md](docs/connecting.md#the-serve-command).

## Standards

This repo follows the [robotsix stack standards](https://github.com/damien-robotsix/robotsix-standards).

## License

This project is licensed under the MIT License (SPDX: `MIT`). See [LICENSE](LICENSE) for the full text.

## Security

To report a security vulnerability, see our [security policy](SECURITY.md).
