The mail ingester no longer crash-loops when no account has ``ingest_mode: watch``.
It now starts healthy and idle, waiting for accounts to be added via the web UI
or config file.
