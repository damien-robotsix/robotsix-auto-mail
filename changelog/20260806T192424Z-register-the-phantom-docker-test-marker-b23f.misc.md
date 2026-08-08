Drop the dead `-m "not docker"` pytest filter from CI (no `docker` marker exists or is registered) and enable `--strict-markers` so unregistered markers fail instead of silently selecting nothing.
