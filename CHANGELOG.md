# Changelog

## 0.0.0 (unreleased)

- Initial package scaffold.
- IMAP/SMTP mail automation with triage and kanban workflows.
- Continuous deployment for `server.robotsix.net`: `release.yml` now publishes
  a moving `main` image on every push to `main`, and a new `deploy/` stack
  (Watchtower auto-update + nginx TLS/basic-auth reverse proxy) serves the
  board at `mail.robotsix.net`. See `deploy/README.md`. Watchtower pins
  `DOCKER_API_VERSION=1.44` for Docker Engine 29+ compatibility; the nginx
  runbook uses the certbot `--nginx` installer and documents the UID-1000
  bind-mount ownership step.
