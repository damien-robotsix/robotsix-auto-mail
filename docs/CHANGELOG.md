# Changelog

All notable changes to `robotsix-auto-mail` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Archive subfolder proposal engine.** When a mail is placed in the
  TO_ARCHIVE column, the system now deterministically proposes a subfolder
  path under the archive root based on (in priority order) mailing-list
  prefix, sender domain+local-part, or date. An LLM-suggested subfolder
  (from the triage agent) takes precedence over the deterministic proposal,
  and a user override takes precedence over everything. See
  [docs/connecting.md](connecting.md#archive-subfolder-proposals-on-the-board).
- **Inline archive-proposal display on TO_ARCHIVE cards.** Every card in the
  TO_ARCHIVE column shows its effective archive destination
  (`<archive_root>/<subfolder>`), a checkmark when the folder already exists,
  and an inline text input + **Set** button to override the subfolder per
  message. Overrides are persisted in the watermark table.
- **`GET /archive-proposal/<message_id>` endpoint** returns structured JSON
  with the effective subfolder, source (`override` / `llm` / `rule`),
  folder-existence status, and override flag.
- **`POST /archive-proposal` endpoint** stores a user override (or clears it
  when the subfolder is empty) and redirects to `/board`.
- **LLM archive-subfolder awareness.** When the `archive_structure` watermark
  is populated, the triage system prompt lists available folders and invites
  the LLM to optionally set an `archive_subfolder` field on
  `TO_ARCHIVE` items. LLM hints are persisted and surface on the board.
- **Deterministic rule `propose_archive_subfolder()`** — purely local, no
  LLM needed. Derives subfolder paths from mailing-list brackets, sender
  email, or date.
- **User override / LLM hint storage** follows the same watermark-table
  pattern as the rule ledger: `archive_subfolder_overrides` and
  `archive_subfolder_llm_hints` keys.
- Mail ingestion pipeline: fetch messages from an IMAP inbox, parse them
  into structured records, and store them idempotently in a local SQLite
  database. See [docs/ingestion.md](ingestion.md).
- CLI (`robotsix-auto-mail`) exposing the `probe`, `ingest`, `detect`, and
  `serve` subcommands. See [docs/connecting.md](connecting.md).
- MX-record provider detection and auto-configuration lookup (Mozilla
  ISPDB, autoconfig XML, with an LLM fallback).
- Self-managed archive folder structure proposed on first run and
  remembered via the watermark table.
- LLM-driven inbox triage agent that classifies mail into advisory action
  statuses (answer / archive / delete / ignore / user_triage) without
  moving mail in the mailbox.
- Read-only/read-write HTTP kanban board exposing the mail database as a
  web UI, with per-card Move dropdowns and a 30-second auto-refresh.
- Optional LLM-driven config-drift advisory agent complementing the
  deterministic `check_config_sync` gate.

[Unreleased]: https://github.com/damien-robotsix/robotsix-auto-mail
