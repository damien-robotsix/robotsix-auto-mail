"""Package-wide constants shared across submodules."""

from typing import Final

#: Root folder under which all managed archive folders live.
_ARCHIVE_ROOT = "robotsix-mail-archive"

#: Default absolute root directory for all persisted mail data
#: (SQLite databases, heartbeat files, etc.).  Relative ``db_path``
#: values are resolved against this root rather than the process CWD
#: so a container restart never silently discards mail databases.
_MAIL_DATA_ROOT: Final[str] = "/data"

#: Watermark keys used by background worker single-flight guards.
_TRIAGE_RUN_STATE_KEY = "triage_run:state"
_BATCH_OP_STATE_KEY = "batch_op:state"
_RECONCILE_STATE_KEY = "reconcile:state"
_INGEST_RUN_STATE_KEY = "ingest_run:state"

#: Watermark sentinel values — the two canonical states for single-flight guards.
_WATERMARK_RUNNING: Final = "running"
_WATERMARK_IDLE: Final = "idle"

_ARCHIVE_TAXONOMY_GUIDANCE = (
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

# -- Referenced by other modules; silence py/unused-global-variable --
_ = (
    _ARCHIVE_ROOT,
    _MAIL_DATA_ROOT,
    _TRIAGE_RUN_STATE_KEY,
    _BATCH_OP_STATE_KEY,
    _RECONCILE_STATE_KEY,
    _INGEST_RUN_STATE_KEY,
    _WATERMARK_RUNNING,
    _WATERMARK_IDLE,
    _ARCHIVE_TAXONOMY_GUIDANCE,
)
