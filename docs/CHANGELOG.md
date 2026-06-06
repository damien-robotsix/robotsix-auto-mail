# Changelog

All notable changes to `robotsix-auto-mail` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
