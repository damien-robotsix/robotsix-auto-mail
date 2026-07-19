# robotsix-auto-mail

`robotsix-auto-mail` is a dedicated module for automated email processing:
it fetches messages from an IMAP inbox, parses them into structured records,
stores them idempotently in a local SQLite database, and exposes a kanban board for reviewing and triaging ingested mail — removing manual email steps from
automated workflows.

## Start here

- [Architecture](architecture.md) — system design, module relationships, and
  the ingestion data flow.
- [Connecting](connecting.md) — configuration keys, precedence rules, and the
  `probe`, `ingest`, and `serve` commands.
- [Deployment](deployment.md) — running the project and its Docker stack.

## Quick start (native)

### 1. Clone and install

```sh
git clone https://github.com/damien-robotsix/robotsix-auto-mail.git
cd robotsix-auto-mail
uv sync --frozen
```

### 2. Create your config

```sh
cp docs/config/mail.local.example.yaml config/mail.local.yaml
$EDITOR config/mail.local.yaml
```

Fill in your IMAP/SMTP host, username, and password (see
[Configuration](configuration.md) for all keys).

### 3. Verify connectivity

```sh
uv run robotsix-auto-mail probe
```

This opens an authenticated IMAP connection and prints the server greeting,
capabilities, and folder listing — no mail is read or sent.

### 4. Fetch your mail

```sh
uv run robotsix-auto-mail ingest
```

This fetches messages from INBOX and stores them idempotently in the local
SQLite database.

### 5. View your mailbox

```sh
uv run robotsix-auto-mail board
```

This prints an inbox view with sender, subject, date, and body preview for
each message.

---

For the Docker-based workflow, see the
[Quick start — Docker Compose](connecting.md#quick-start--docker-compose-recommended)
section in the connecting guide.
