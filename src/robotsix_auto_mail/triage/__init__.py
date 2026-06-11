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
    _AGENT_SELECTABLE_ACTIONS,
    _ARCHIVE_LLM_HINTS_WATERMARK_KEY,
    _ARCHIVE_OVERRIDES_WATERMARK_KEY,
    _MEMORY_WATERMARK_KEY,
    _RULE_ACTIVE_WATERMARK_KEY,
    _RULE_LEDGER_WATERMARK_KEY,
    _RULE_MIN_DECISIONS,
    _UNSUBSCRIBE_SUGGESTIONS_KEY,
    _VALID_CONFIDENCE_LEVELS,
    _VALID_RULE_MATCH_TYPES,
    _VALID_RULE_STATES,
    _VALID_TRIAGE_SOURCES,
    TRIAGE_ACTION_LABELS,
    TRIAGE_ACTION_ORDER,
)
from robotsix_auto_mail.triage.agent import (
    _body_preview,
    _build_triage_system_prompt,
    _build_user_message,
    _check_unsubscribe_for_to_delete,
    _detect_unsubscribe_for_sender,
    run_triage_agent,
)
from robotsix_auto_mail.triage.classifier import (
    _build_memory_guidance,
    _confidence_for,
    _domain_key,
    _load_active_rules,
    _load_archive_overrides,
    _load_llm_archive_hints,
    _load_memory,
    _load_rule_ledger,
    _rule_fingerprint,
    _rule_matches,
    _sanitise_subfolder,
    _save_active_rules,
    _save_archive_overrides,
    _save_llm_archive_hints,
    _save_memory,
    _save_rule_ledger,
    _sender_key,
    apply_triage_rules,
    get_archive_subfolder,
    list_rule_proposals,
    propose_archive_subfolder,
    propose_archive_subfolder_llm,
    propose_triage_rules,
    record_and_filter_rule_proposals,
    record_human_decision,
    set_archive_subfolder_override,
    set_rule_state,
)
from robotsix_auto_mail.triage.persistence import (
    ArchiveSubfolderProposal,
    RuleLedgerEntry,
    SenderMemory,
    TriageDecision,
    TriageError,
    TriageItem,
    TriageResult,
    TriageRule,
    TriageRuleProposal,
    UnsubscribeDetection,
    _utc_now_iso,
    delete_triage_decisions_by_action,
    get_triage_decision,
    list_triage_decisions,
    set_triage_decision,
)

__all__ = [
    "TRIAGE_ACTION_LABELS",
    "TRIAGE_ACTION_ORDER",
    "VALID_TRIAGE_ACTIONS",
    "_AGENT_SELECTABLE_ACTIONS",
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
    "_load_active_rules",
    "_load_archive_overrides",
    "_load_llm_archive_hints",
    "_load_memory",
    "_load_rule_ledger",
    "_rule_fingerprint",
    "_rule_matches",
    "_sanitise_subfolder",
    "_save_active_rules",
    "_save_archive_overrides",
    "_save_llm_archive_hints",
    "_save_memory",
    "_save_rule_ledger",
    "_sender_key",
    "_utc_now_iso",
    "apply_triage_rules",
    "delete_triage_decisions_by_action",
    "get_archive_subfolder",
    "get_triage_decision",
    "list_rule_proposals",
    "list_triage_decisions",
    "propose_archive_subfolder",
    "propose_archive_subfolder_llm",
    "propose_triage_rules",
    "record_and_filter_rule_proposals",
    "record_human_decision",
    "run_triage_agent",
    "set_archive_subfolder_override",
    "set_rule_state",
    "set_triage_decision",
]
