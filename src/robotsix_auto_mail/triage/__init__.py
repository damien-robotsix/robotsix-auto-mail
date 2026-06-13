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

The implementation is split across internal submodules:

- ``_constants`` — shared action constants and watermark keys.
- ``persistence`` — pydantic models and triage-decision storage.
- ``classifier`` — deterministic archive-subfolder, memory and rules.
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
    _AGENT_SELECTABLE_ACTIONS as _AGENT_SELECTABLE_ACTIONS,
)
from robotsix_auto_mail.triage._constants import (
    _ARCHIVE_FOLDER_MEMORY_WATERMARK_KEY as _ARCHIVE_FOLDER_MEMORY_WATERMARK_KEY,
)
from robotsix_auto_mail.triage._constants import (
    _ARCHIVE_LLM_HINTS_WATERMARK_KEY as _ARCHIVE_LLM_HINTS_WATERMARK_KEY,
)
from robotsix_auto_mail.triage._constants import (
    _ARCHIVE_OVERRIDES_WATERMARK_KEY as _ARCHIVE_OVERRIDES_WATERMARK_KEY,
)
from robotsix_auto_mail.triage._constants import (
    _MEMORY_WATERMARK_KEY as _MEMORY_WATERMARK_KEY,
)
from robotsix_auto_mail.triage._constants import (
    _RULE_ACTIVE_WATERMARK_KEY as _RULE_ACTIVE_WATERMARK_KEY,
)
from robotsix_auto_mail.triage._constants import (
    _RULE_LEDGER_WATERMARK_KEY as _RULE_LEDGER_WATERMARK_KEY,
)
from robotsix_auto_mail.triage._constants import (
    _RULE_MIN_DECISIONS as _RULE_MIN_DECISIONS,
)
from robotsix_auto_mail.triage._constants import (
    _UNSUBSCRIBE_SUGGESTIONS_KEY as _UNSUBSCRIBE_SUGGESTIONS_KEY,
)
from robotsix_auto_mail.triage._constants import (
    _VALID_CONFIDENCE_LEVELS as _VALID_CONFIDENCE_LEVELS,
)
from robotsix_auto_mail.triage._constants import (
    _VALID_RULE_MATCH_TYPES as _VALID_RULE_MATCH_TYPES,
)
from robotsix_auto_mail.triage._constants import (
    _VALID_RULE_STATES as _VALID_RULE_STATES,
)
from robotsix_auto_mail.triage._constants import (
    _VALID_TRIAGE_SOURCES as _VALID_TRIAGE_SOURCES,
)
from robotsix_auto_mail.triage._constants import (
    TRIAGE_ACTION_LABELS as TRIAGE_ACTION_LABELS,
)
from robotsix_auto_mail.triage._constants import (
    TRIAGE_ACTION_ORDER as TRIAGE_ACTION_ORDER,
)
from robotsix_auto_mail.triage.agent import (
    _body_preview as _body_preview,
)
from robotsix_auto_mail.triage.agent import (
    _build_triage_system_prompt as _build_triage_system_prompt,
)
from robotsix_auto_mail.triage.agent import (
    _build_user_message as _build_user_message,
)
from robotsix_auto_mail.triage.agent import (
    _check_unsubscribe_for_to_delete as _check_unsubscribe_for_to_delete,
)
from robotsix_auto_mail.triage.agent import (
    _detect_unsubscribe_for_sender as _detect_unsubscribe_for_sender,
)
from robotsix_auto_mail.triage.agent import (
    _is_non_semantic_subfolder as _is_non_semantic_subfolder,
)
from robotsix_auto_mail.triage.agent import (
    _load_archive_guidance as _load_archive_guidance,
)
from robotsix_auto_mail.triage.agent import (
    run_triage_agent as run_triage_agent,
)
from robotsix_auto_mail.triage.classifier import (
    _build_memory_guidance as _build_memory_guidance,
)
from robotsix_auto_mail.triage.classifier import (
    _confidence_for as _confidence_for,
)
from robotsix_auto_mail.triage.classifier import (
    _domain_key as _domain_key,
)
from robotsix_auto_mail.triage.classifier import (
    _load_active_rules as _load_active_rules,
)
from robotsix_auto_mail.triage.classifier import (
    _load_archive_folder_memory as _load_archive_folder_memory,
)
from robotsix_auto_mail.triage.classifier import (
    _load_archive_overrides as _load_archive_overrides,
)
from robotsix_auto_mail.triage.classifier import (
    _load_llm_archive_hints as _load_llm_archive_hints,
)
from robotsix_auto_mail.triage.classifier import (
    _load_memory as _load_memory,
)
from robotsix_auto_mail.triage.classifier import (
    _load_rule_ledger as _load_rule_ledger,
)
from robotsix_auto_mail.triage.classifier import (
    _rule_fingerprint as _rule_fingerprint,
)
from robotsix_auto_mail.triage.classifier import (
    _rule_matches as _rule_matches,
)
from robotsix_auto_mail.triage.classifier import (
    _sanitise_subfolder as _sanitise_subfolder,
)
from robotsix_auto_mail.triage.classifier import (
    _save_active_rules as _save_active_rules,
)
from robotsix_auto_mail.triage.classifier import (
    _save_archive_folder_memory as _save_archive_folder_memory,
)
from robotsix_auto_mail.triage.classifier import (
    _save_archive_overrides as _save_archive_overrides,
)
from robotsix_auto_mail.triage.classifier import (
    _save_llm_archive_hints as _save_llm_archive_hints,
)
from robotsix_auto_mail.triage.classifier import (
    _save_memory as _save_memory,
)
from robotsix_auto_mail.triage.classifier import (
    _save_rule_ledger as _save_rule_ledger,
)
from robotsix_auto_mail.triage.classifier import (
    _sender_key as _sender_key,
)
from robotsix_auto_mail.triage.classifier import (
    apply_triage_rules as apply_triage_rules,
)
from robotsix_auto_mail.triage.classifier import (
    delete_active_rule as delete_active_rule,
)
from robotsix_auto_mail.triage.classifier import (
    get_archive_subfolder as get_archive_subfolder,
)
from robotsix_auto_mail.triage.classifier import (
    list_rule_proposals as list_rule_proposals,
)
from robotsix_auto_mail.triage.classifier import (
    normalize_archive_subfolder as normalize_archive_subfolder,
)
from robotsix_auto_mail.triage.classifier import (
    propose_archive_subfolder as propose_archive_subfolder,
)
from robotsix_auto_mail.triage.classifier import (
    propose_archive_subfolder_llm as propose_archive_subfolder_llm,
)
from robotsix_auto_mail.triage.classifier import (
    propose_triage_rules as propose_triage_rules,
)
from robotsix_auto_mail.triage.classifier import (
    record_and_filter_rule_proposals as record_and_filter_rule_proposals,
)
from robotsix_auto_mail.triage.classifier import (
    record_archive_folder_choice as record_archive_folder_choice,
)
from robotsix_auto_mail.triage.classifier import (
    record_human_decision as record_human_decision,
)
from robotsix_auto_mail.triage.classifier import (
    set_archive_subfolder_override as set_archive_subfolder_override,
)
from robotsix_auto_mail.triage.classifier import (
    set_rule_state as set_rule_state,
)
from robotsix_auto_mail.triage.persistence import (
    ArchiveFolderMemory as ArchiveFolderMemory,
)
from robotsix_auto_mail.triage.persistence import (
    ArchiveSubfolderProposal as ArchiveSubfolderProposal,
)
from robotsix_auto_mail.triage.persistence import (
    RuleLedgerEntry as RuleLedgerEntry,
)
from robotsix_auto_mail.triage.persistence import (
    SenderMemory as SenderMemory,
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
    TriageRule as TriageRule,
)
from robotsix_auto_mail.triage.persistence import (
    TriageRuleProposal as TriageRuleProposal,
)
from robotsix_auto_mail.triage.persistence import (
    UnsubscribeDetection as UnsubscribeDetection,
)
from robotsix_auto_mail.triage.persistence import (
    _utc_now_iso as _utc_now_iso,
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

__all__ = [
    "TRIAGE_ACTION_LABELS",
    "TRIAGE_ACTION_ORDER",
    "VALID_TRIAGE_ACTIONS",
    "_AGENT_SELECTABLE_ACTIONS",
    "_ARCHIVE_FOLDER_MEMORY_WATERMARK_KEY",
    "_ARCHIVE_LLM_HINTS_WATERMARK_KEY",
    "_ARCHIVE_OVERRIDES_WATERMARK_KEY",
    "_MEMORY_WATERMARK_KEY",
    "_RULE_ACTIVE_WATERMARK_KEY",
    "_RULE_LEDGER_WATERMARK_KEY",
    "_RULE_MIN_DECISIONS",
    "_UNSUBSCRIBE_SUGGESTIONS_KEY",
    "_VALID_CONFIDENCE_LEVELS",
    "_VALID_RULE_MATCH_TYPES",
    "_VALID_RULE_STATES",
    "_VALID_TRIAGE_SOURCES",
    "ArchiveFolderMemory",
    "ArchiveSubfolderProposal",
    "RuleLedgerEntry",
    "SenderMemory",
    "TriageDecision",
    "TriageError",
    "TriageItem",
    "TriageResult",
    "TriageRule",
    "TriageRuleProposal",
    "UnsubscribeDetection",
    "_body_preview",
    "_build_memory_guidance",
    "_build_triage_system_prompt",
    "_build_user_message",
    "_check_unsubscribe_for_to_delete",
    "_confidence_for",
    "_detect_unsubscribe_for_sender",
    "_domain_key",
    "_is_non_semantic_subfolder",
    "_load_active_rules",
    "_load_archive_folder_memory",
    "_load_archive_guidance",
    "_load_archive_overrides",
    "_load_llm_archive_hints",
    "_load_memory",
    "_load_rule_ledger",
    "_rule_fingerprint",
    "_rule_matches",
    "_sanitise_subfolder",
    "_save_active_rules",
    "_save_archive_folder_memory",
    "_save_archive_overrides",
    "_save_llm_archive_hints",
    "_save_memory",
    "_save_rule_ledger",
    "_sender_key",
    "_utc_now_iso",
    "apply_triage_rules",
    "delete_active_rule",
    "delete_triage_decision",
    "delete_triage_decisions_by_action",
    "get_archive_subfolder",
    "get_triage_decision",
    "list_rule_proposals",
    "list_triage_decisions",
    "normalize_archive_subfolder",
    "propose_archive_subfolder",
    "propose_archive_subfolder_llm",
    "propose_triage_rules",
    "record_and_filter_rule_proposals",
    "record_archive_folder_choice",
    "record_human_decision",
    "run_triage_agent",
    "set_archive_subfolder_override",
    "set_rule_state",
    "set_triage_decision",
]
