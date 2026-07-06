Consolidate five near-identical single-column UPDATE functions in ``db.queries`` into a shared ``_update_column`` helper, reducing ~100 lines of copy-paste to ~32 while preserving the public API.
