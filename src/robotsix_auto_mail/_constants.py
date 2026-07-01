"""Package-wide constants shared across submodules."""

#: Root folder under which all managed archive folders live.
_ARCHIVE_ROOT = "robotsix-mail-archive"  # lgtm[py/unused-global-variable]

#: Watermark keys used by background worker single-flight guards.
_TRIAGE_RUN_STATE_KEY = "triage_run:state"  # lgtm[py/unused-global-variable]
_BATCH_OP_STATE_KEY = "batch_op:state"  # lgtm[py/unused-global-variable]
_RECONCILE_STATE_KEY = "reconcile:state"  # lgtm[py/unused-global-variable]

_ARCHIVE_TAXONOMY_GUIDANCE = (  # lgtm[py/unused-global-variable]
    "Categorize by purpose or topic: choose a top-level semantic "
    "bucket adapted to the existing folders. Example buckets "
    "(adapt to the user's existing structure — these are not a fixed "
    "list): `Finance` (invoices, receipts, bank), `Orders` "
    "(purchases, shipping), `Travel`, `Newsletters`, `Notifications` "
    "(CI / automated alerts), `Projects/<name>`, `Admin` (accounts, "
    "legal). Do NOT use bare `<domain>/<sender>` paths (e.g. never "
    "`lwn.net/lwn`); a sender name may appear only as a leaf under a "
    "semantic parent (e.g. `Newsletters/LWN`) and only when no better "
    "topical bucket fits. Keep paths shallow: at most 2 levels (one "
    "`/` separator)."
)
