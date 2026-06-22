# Changelog

## 0.0.0 (unreleased)

- Added AGENT.md with repository conventions for CI-fix and other automated agents.
- Added structured access logging to the HTTP server via ``log_message``.
- Migrated logging to delegate core pipeline to ``robotsix_llmio.logging.setup_logging``
  (stream handler, formatter, OTel trace-id injection), retaining only the
  date-stamped file handler in the local ``setup_logging`` wrapper.
- Added changelog-enforcer CI job to gate pull requests.
- Initial package scaffold.
- IMAP/SMTP mail automation with triage and kanban workflows.
- Continuous deployment for `server.robotsix.net`: `release.yml` now publishes
  a moving `main` image on every push to `main`, and a new `deploy/` stack
  (Watchtower auto-update + nginx TLS/basic-auth reverse proxy) serves the
  board at `mail.robotsix.net`. See `deploy/README.md`. Watchtower pins
  `DOCKER_API_VERSION=1.44` for Docker Engine 29+ compatibility; the nginx
  runbook uses the certbot `--nginx` installer and documents the UID-1000
  bind-mount ownership step.
