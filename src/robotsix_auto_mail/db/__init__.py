"""Local SQLite datastore for ingested mail messages and watermark tracking.

The implementation is split across internal submodules:

- ``models`` — the ``MailRecord`` frozen dataclass, DDL schema, and
  canonical triage vocabulary.
- ``queries`` — CRUD and query functions (``init_db``, ``insert_record``,
  ``list_records``, watermark management, etc.).
- ``_migrate`` — additive column migrations and legacy status remapping.
- ``archive`` — self-managed archive folder structure and LLM-driven layout.

This module re-exports the public and previously-importable symbols so
``from robotsix_auto_mail.db import ...`` keeps working unchanged.
"""

from __future__ import annotations

from .archive import (
    ARCHIVE_ROOT as ARCHIVE_ROOT,
)
from .archive import (
    ArchiveError as ArchiveError,
)
from .archive import (
    ArchiveStructure as ArchiveStructure,
)
from .archive import (
    determine_archive_structure as determine_archive_structure,
)
from .archive import (
    setup_archive as setup_archive,
)
from .models import (
    _SCHEMA as _SCHEMA,
)
from .models import (
    _TRIAGE_ACTION_CHECK_VALUES as _TRIAGE_ACTION_CHECK_VALUES,
)
from .models import (
    VALID_TRIAGE_ACTIONS as VALID_TRIAGE_ACTIONS,
)
from .models import (
    MailRecord as MailRecord,
)
from .queries import (
    component_settings_count as component_settings_count,
)
from .queries import (
    delete_record_by_message_id as delete_record_by_message_id,
)
from .queries import (
    delete_watermark as delete_watermark,
)
from .queries import (
    get_account_health as get_account_health,
)
from .queries import (
    get_all_component_settings as get_all_component_settings,
)
from .queries import (
    get_component_setting as get_component_setting,
)
from .queries import (
    get_record_by_message_id as get_record_by_message_id,
)
from .queries import (
    get_watermark as get_watermark,
)
from .queries import (
    init_db as init_db,
)
from .queries import (
    insert_record as insert_record,
)
from .queries import (
    list_records as list_records,
)
from .queries import (
    list_untriaged_records as list_untriaged_records,
)
from .queries import (
    load_json_watermark as load_json_watermark,
)
from .queries import (
    record_exists as record_exists,
)
from .queries import (
    save_json_watermark as save_json_watermark,
)
from .queries import (
    set_component_setting as set_component_setting,
)
from .queries import (
    set_component_settings as set_component_settings,
)
from .queries import (
    set_watermark as set_watermark,
)
from .queries import (
    update_calendar_correlation_id as update_calendar_correlation_id,
)
from .queries import (
    update_calendar_event_ref as update_calendar_event_ref,
)
from .queries import (
    update_draft_text as update_draft_text,
)
from .queries import (
    update_notes as update_notes,
)
from .queries import (
    update_record_source as update_record_source,
)
from .queries import (
    update_sent_reply_text as update_sent_reply_text,
)
from .queries import (
    write_account_health as write_account_health,
)

__all__ = [
    "ARCHIVE_ROOT",
    "VALID_TRIAGE_ACTIONS",
    "ArchiveError",
    "ArchiveStructure",
    "MailRecord",
    "component_settings_count",
    "delete_record_by_message_id",
    "delete_watermark",
    "determine_archive_structure",
    "get_account_health",
    "get_all_component_settings",
    "get_component_setting",
    "get_record_by_message_id",
    "get_watermark",
    "init_db",
    "insert_record",
    "list_records",
    "list_untriaged_records",
    "load_json_watermark",
    "record_exists",
    "save_json_watermark",
    "set_component_setting",
    "set_component_settings",
    "set_watermark",
    "setup_archive",
    "update_calendar_correlation_id",
    "update_calendar_event_ref",
    "update_draft_text",
    "update_notes",
    "update_record_source",
    "update_sent_reply_text",
    "write_account_health",
]
