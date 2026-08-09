# vulture
# vulture_whitelist.py — legitimate false positives for dead-code detection
#
# This file records every vulture finding that is NOT dead code plus known
# dead code that is intentionally deferred to a separate removal ticket.
# Each entry references the flagged name so vulture considers it "used".
#
# Format: import the module/class, then reference the name.
# For class-level items: ``from module import Class; Class.attr``
# For module-level items: ``from module import name; name``

# ===========================================================================
# Pydantic @field_validator methods — called by pydantic via the decorator,
# never invoked directly by application code.
# ===========================================================================

from robotsix_auto_mail.config.config_sync_agent import DriftProposal

DriftProposal._validate_confidence

from robotsix_auto_mail.config.config_sync_agent import LedgerEntry

LedgerEntry._validate_state

from robotsix_auto_mail.config.detect import DetectedProvider

DetectedProvider._validate_tls_mode

from robotsix_auto_mail.triage.persistence import TriageItem

TriageItem._coerce_action
TriageItem._validate_confidence

from robotsix_auto_mail.triage.persistence import TriageDecision

TriageDecision._validate_action
TriageDecision._validate_source

from robotsix_auto_mail.triage.persistence import UnsubscribeDetection

UnsubscribeDetection._validate_method

# ===========================================================================
# Framework overrides — called by the parent class / stdlib framework.
# ===========================================================================

from robotsix_auto_mail.server.handlers import BoardHandler

BoardHandler.do_GET
BoardHandler.do_POST
BoardHandler.do_PUT
BoardHandler.log_message

# ===========================================================================
# Duck-typing / protocol methods — called by robotsix-board via getattr.
# ===========================================================================

from robotsix_auto_mail.server.board_adapter import BoardAdapter

BoardAdapter.card_id
BoardAdapter.card_title
BoardAdapter.card_badges
BoardAdapter.card_timestamps
BoardAdapter.move_endpoint_template
BoardAdapter.render_mode
BoardAdapter.card_extra_html
BoardAdapter.column_extra_html

# ===========================================================================
# Pydantic model fields — accessed via model_dump / model_validate / keyword
# construction, never read as plain class attributes by application code.
# ===========================================================================

from robotsix_auto_mail.config.model import MailConfig

MailConfig._validate_template_literals
MailConfig.model_config
MailConfig.oauth2_client_secret
MailConfig._validate_imap_tls_mode
MailConfig._validate_smtp_tls_mode
MailConfig._validate_log_level
MailConfig._validate_log_format

from robotsix_auto_mail.config.model import MailAccountConfig

MailAccountConfig.model_config
MailAccountConfig._validate_account_id

from robotsix_auto_mail.config.model import MailAccountsConfig

MailAccountsConfig.model_config
MailAccountsConfig._validate
MailAccountsConfig.models
MailAccountsConfig.triage_level
MailAccountsConfig.classifier_level
MailAccountsConfig.rules_level
MailAccountsConfig.detector_level
MailAccountsConfig.draft_level

from robotsix_auto_mail.config.model import TierModelsConfig

TierModelsConfig.model_config
TierModelsConfig.level1
TierModelsConfig.level2
TierModelsConfig.level3
TierModelsConfig.level4

# ===========================================================================
# Config field mapping — imported by check_config_sync.py, not used directly
# in this module, but vulture doesn't trace cross-module imports.
# ===========================================================================

from robotsix_auto_mail.config._field_map import FIELD_YAML_MAP

_ = FIELD_YAML_MAP

# ===========================================================================
# Public API function — exported from config.loader, called by external
# consumers (CLI, server); vulture doesn't trace cross-module usage.
# ===========================================================================

from robotsix_auto_mail.config.loader import (
    get_resolved_models,
    resolve_application_level,
    resolve_llm_tier,
    resolve_model_override,
    save_accounts,
)

all((  # lgtm[py/ineffectual-statement]
    get_resolved_models,
    resolve_application_level,
    resolve_llm_tier,
    resolve_model_override,
    save_accounts,
))

# ===========================================================================
# SettingsStore — methods called by tests and internally; vulture doesn't
# trace test usage or attribute access inside the same module.
# ===========================================================================

from robotsix_auto_mail.settings.store import SettingsStore

all((  # lgtm[py/ineffectual-statement]
    SettingsStore._db_path,
    SettingsStore.seed_from_mail_config,
    SettingsStore.to_mail_config,
))

# ===========================================================================
# HTMLParser callback methods — called by the parent class's feed() method,
# never invoked directly by application code.
# ===========================================================================

from robotsix_auto_mail.core._sanitize import _SanitizingParser

_SanitizingParser.handle_starttag
_SanitizingParser.handle_endtag
_SanitizingParser.handle_data
_SanitizingParser.handle_entityref
_SanitizingParser.handle_charref

# ===========================================================================
# Canonical credential blocks — ``project_id`` is part of the storage shape
# robotsix-standards fixes for every component; it exists for the fleet
# consumers that address a Langfuse project by id, and this component never
# reads it itself.
# ===========================================================================

from robotsix_auto_mail.config.credentials import LangfuseProject

all((LangfuseProject.project_id,))  # lgtm[py/ineffectual-statement]
