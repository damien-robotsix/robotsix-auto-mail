Fixed the Docs workflow, which had never once run. Its caller granted
`contents: write`, but the shared docs spine deploys through the Pages Actions
and needs `contents: read` plus `pages: write` and `id-token: write`. A caller's
permissions map replaces rather than merges, so all three were unmet — and an
unmet request fails the run at startup, producing no logs and no checks, which
is why nothing surfaced it.
