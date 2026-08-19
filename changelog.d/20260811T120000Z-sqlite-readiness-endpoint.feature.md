Add `GET /ready` and `GET /readyz` readiness endpoints.

- `_serve_ready()` verifies the SQLite store is reachable via `SELECT 1` and returns `{"status": "ready"}` (200) or `{"status": "unavailable", "error": "..."}` (503).
- Routes registered in `do_GET` alongside existing liveness endpoints `/health` and `/healthz`.
- Container liveness probe stays on `/health` (unchanged); readiness is consumed by the gateway/monitoring.
