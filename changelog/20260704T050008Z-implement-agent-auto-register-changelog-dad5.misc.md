Update implement agent prompt (AGENT.md) to require registering new
changelog fragments in `docs/modules.yaml` under the `core` module's
`paths` list.  This eliminates a recurring source of CI noise where
the implement agent creates a fragment but forgets to register it,
triggering a `robotsix-modules check-registration` failure and a
follow-up CI-fix commit.
