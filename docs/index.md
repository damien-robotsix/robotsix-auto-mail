# robotsix-auto-mail

`robotsix-auto-mail` is a dedicated module for automated email processing:
it fetches messages from an IMAP inbox, parses them into structured records,
stores them idempotently in a local SQLite database, and exposes a read-only
kanban board for triaging ingested mail — removing manual email steps from
automated workflows.

## Start here

- [Architecture](architecture.md) — system design, module relationships, and
  the ingestion data flow.
- [Connecting](connecting.md) — configuration keys, precedence rules, and the
  `probe`, `ingest`, and `serve` commands.
- [Deployment](deployment.md) — running the project and its Docker stack.
