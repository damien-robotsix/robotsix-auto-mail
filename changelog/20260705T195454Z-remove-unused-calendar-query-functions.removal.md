Removed unused `update_calendar_event_ref` and `update_calendar_correlation_id`
query functions from `db/queries.py`, their re-exports from `db/__init__.py`,
and cleaned up phantom `SenderMemory`/`ArchiveFolderMemory` references in
`vulture_whitelist.py`. Calendar columns (`calendar_event_ref`,
`calendar_correlation_id`) remain in the schema, reserved for future
calendar-automation use.
