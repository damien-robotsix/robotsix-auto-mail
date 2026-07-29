Fix CSRF guard rejecting same-origin POSTs behind a reverse proxy by comparing the request's Origin header against its Host header (proxy-aware same-origin check)
