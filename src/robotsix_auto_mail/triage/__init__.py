"""LLM-driven inbox triage agent and local triage-decision persistence.

The triage agent classifies each ingested inbox ``MailRecord`` into one of
four agent-selectable *triage actions* — ``HUMAN_TRIAGE`` / ``TO_ARCHIVE``
/ ``TO_DELETE`` / ``TO_ANSWER`` — with ``HUMAN_TRIAGE`` as the explicit
"the system does not know what to do" fallback.  The fifth action,
``INBOX``, is reserved for not-yet-triaged mail (the board entry column)
and may only be set by a human moving a card — the agent never assigns it
(see :data:`_AGENT_SELECTABLE_ACTIONS`).  These actions are stored in the
``triage_decisions`` table.  Triage performs **NO IMAP /
mailbox side effects** whatsoever: the kanban is a local-only board, so
moving a card never touches the original mailbox (no archive / delete /
move / expunge / append / store).

Learned preferences come from the human-readable ``triage_rules.md`` file
(see :mod:`robotsix_auto_mail.triage.rules`), which a flash LLM maintains
from the user's board actions and the triage agent reads on every run.

The implementation is split across internal submodules:

- ``_constants`` — shared action constants and watermark keys.
- ``persistence`` — pydantic models and triage-decision storage.
- ``classifier`` — deterministic archive-subfolder proposal + per-message
  override / LLM-hint caches and sender-key helpers.
- ``rules`` — the ``triage_rules.md`` file maintained by the flash LLM.
- ``agent`` — the LLM triage agent (``run_triage_agent``); its
  ``pydantic_ai`` imports stay lazy to keep module-load time low.

This module re-exports the public and previously-importable symbols so
``from robotsix_auto_mail.triage import ...`` keeps working unchanged.
"""

from __future__ import annotations

from robotsix_auto_mail.db import (
    VALID_TRIAGE_ACTIONS as VALID_TRIAGE_ACTIONS,
)
from robotsix_auto_mail.triage._constants import (
    DRAFT_READY as DRAFT_READY,
)
from robotsix_auto_mail.triage._constants import (
    HUMAN_TRIAGE as HUMAN_TRIAGE,
)
from robotsix_auto_mail.triage._constants import (
    INBOX as INBOX,
)
from robotsix_auto_mail.triage._constants import (
    PENDING_ACTION as PENDING_ACTION,
)
from robotsix_auto_mail.triage._constants import (
    TO_ANSWER as TO_ANSWER,
)
from robotsix_auto_mail.triage._constants import (
    TO_ARCHIVE as TO_ARCHIVE,
)
from robotsix_auto_mail.triage._constants import (
    TO_CALENDAR as TO_CALENDAR,
)
from robotsix_auto_mail.triage._constants import (
    TO_DELETE as TO_DELETE,
)
from robotsix_auto_mail.triage._constants import (
    TRIAGE_ACTION_LABELS as TRIAGE_ACTION_LABELS,
)
from robotsix_auto_mail.triage._constants import (
    TRIAGE_ACTION_ORDER as TRIAGE_ACTION_ORDER,
)
from robotsix_auto_mail.triage.agent import (
    _build_triage_system_prompt as _build_triage_system_prompt,
)
from robotsix_auto_mail.triage.agent import (
    _check_unsubscribe_for_to_delete as _check_unsubscribe_for_to_delete,
)
from robotsix_auto_mail.triage.agent import (
    _detect_unsubscribe_for_sender as _detect_unsubscribe_for_sender,
)
from robotsix_auto_mail.triage.agent import (
    _load_archive_folders as _load_archive_folders,
)
from robotsix_auto_mail.triage.agent import (
    run_triage_agent as run_triage_agent,
)
from robotsix_auto_mail.triage.classifier import (
    _load_archive_overrides as _load_archive_overrides,
)
from robotsix_auto_mail.triage.classifier import (
    _load_llm_archive_hints as _load_llm_archive_hints,
)
from robotsix_auto_mail.triage.classifier import (
    _save_archive_overrides as _save_archive_overrides,
)
from robotsix_auto_mail.triage.classifier import (
    _save_llm_archive_hints as _save_llm_archive_hints,
)
from robotsix_auto_mail.triage.classifier import (
    _sender_key as _sender_key,
)
from robotsix_auto_mail.triage.classifier import (
    get_archive_subfolder as get_archive_subfolder,
)
from robotsix_auto_mail.triage.classifier import (
    propose_archive_subfolder as propose_archive_subfolder,
)
from robotsix_auto_mail.triage.classifier import (
    propose_archive_subfolder_llm as propose_archive_subfolder_llm,
)
from robotsix_auto_mail.triage.classifier import (
    set_archive_subfolder_override as set_archive_subfolder_override,
)
from robotsix_auto_mail.triage.persistence import (
    ArchiveSubfolderProposal as ArchiveSubfolderProposal,
)
from robotsix_auto_mail.triage.persistence import (
    TriageDecision as TriageDecision,
)
from robotsix_auto_mail.triage.persistence import (
    TriageError as TriageError,
)
from robotsix_auto_mail.triage.persistence import (
    TriageItem as TriageItem,
)
from robotsix_auto_mail.triage.persistence import (
    TriageResult as TriageResult,
)
from robotsix_auto_mail.triage.persistence import (
    UnsubscribeDetection as UnsubscribeDetection,
)
from robotsix_auto_mail.triage.persistence import (
    delete_triage_decision as delete_triage_decision,
)
from robotsix_auto_mail.triage.persistence import (
    delete_triage_decisions_by_action as delete_triage_decisions_by_action,
)
from robotsix_auto_mail.triage.persistence import (
    get_triage_decision as get_triage_decision,
)
from robotsix_auto_mail.triage.persistence import (
    list_triage_decisions as list_triage_decisions,
)
from robotsix_auto_mail.triage.persistence import (
    set_triage_decision as set_triage_decision,
)
from robotsix_auto_mail.triage.rules import (
    RulesMarkdown as RulesMarkdown,
)
from robotsix_auto_mail.triage.rules import (
    load_rules as load_rules,
)
from robotsix_auto_mail.triage.rules import (
    record_user_action as record_user_action,
)
from robotsix_auto_mail.triage.rules import (
    resolve_rules_path as resolve_rules_path,
)
from robotsix_auto_mail.triage.rules import (
    rules_text_for as rules_text_for,
)
from robotsix_auto_mail.triage.rules import (
    update_rules_for_action as update_rules_for_action,
)

__all__ = [
    "DRAFT_READY",
    "HUMAN_TRIAGE",
    "INBOX",
    "PENDING_ACTION",
    "TO_ANSWER",
    "TO_ARCHIVE",
    "TO_CALENDAR",
    "TO_DELETE",
    "TRIAGE_ACTION_LABELS",
    "TRIAGE_ACTION_ORDER",
    "VALID_TRIAGE_ACTIONS",
    "ArchiveSubfolderProposal",
    "RulesMarkdown",
    "TriageDecision",
    "TriageError",
    "TriageItem",
    "TriageResult",
    "UnsubscribeDetection",
    "_build_triage_system_prompt",
    "_check_unsubscribe_for_to_delete",
    "_detect_unsubscribe_for_sender",
    "_load_archive_folders",
    "_load_archive_overrides",
    "_load_llm_archive_hints",
    "_save_archive_overrides",
    "_save_llm_archive_hints",
    "_sender_key",
    "delete_triage_decision",
    "delete_triage_decisions_by_action",
    "get_archive_subfolder",
    "get_triage_decision",
    "list_triage_decisions",
    "load_rules",
    "propose_archive_subfolder",
    "propose_archive_subfolder_llm",
    "record_user_action",
    "resolve_rules_path",
    "rules_text_for",
    "run_triage_agent",
    "set_archive_subfolder_override",
    "set_triage_decision",
    "update_rules_for_action",
]
