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

from robotsix_auto_mail.triage.persistence import SenderMemory

SenderMemory._validate_action
SenderMemory.last_action
SenderMemory.updated_at

from robotsix_auto_mail.triage.persistence import UnsubscribeDetection

UnsubscribeDetection._validate_method

from robotsix_auto_mail.triage.persistence import ArchiveFolderMemory

ArchiveFolderMemory.updated_at

# ===========================================================================
# Framework overrides — called by the parent class / stdlib framework.
# ===========================================================================

from robotsix_auto_mail.server.handlers import BoardHandler

BoardHandler.do_GET
BoardHandler.do_POST
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

# ===========================================================================
# Config field mapping — imported by check_config_sync.py, not used directly
# in this module, but vulture doesn't trace cross-module imports.
# ===========================================================================

from robotsix_auto_mail.config._field_map import FIELD_YAML_MAP

_ = FIELD_YAML_MAP

# ===========================================================================
# CLI config helpers — loaded by other modules via import, not directly called
# in this module; vulture doesn't trace cross-module usage.
# ===========================================================================

from robotsix_auto_mail.cli import config

config._existing_account_ids
config._existing_accounts_for_append
config._find_existing_account

# ===========================================================================
# Public API function — exported from config.loader, called by external
# consumers (CLI, server); vulture doesn't trace cross-module usage.
# ===========================================================================

from robotsix_auto_mail.config.loader import save_accounts

save_accounts
