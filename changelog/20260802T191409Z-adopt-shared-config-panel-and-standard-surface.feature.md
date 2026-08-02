Replace the bespoke `/settings` API and hand-written settings panel with the
fleet's standard config surface (`GET`/`PUT /config`, `GET /config/versions`,
`POST /config/rollback`) over `config/config.json`, and mount the shared
`@robotsix/ui` config panel on the Settings page instead of rendering a form
of auto-mail's own. Secrets are now typed from the `SecretStr` fields on the
model rather than guessed from field-name suffixes, updates are partial with
merge-on-write, and every write is versioned with rollback (history never
stores a secret value).
