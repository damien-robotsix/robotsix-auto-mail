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

from robotsix_auto_mail.detect import DetectedProvider
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
# Genuinely dead code — removal deferred to a separate ticket.
# ===========================================================================

from robotsix_auto_mail.config import logger
logger  # noqa

from robotsix_auto_mail.detect import ProviderEntry
ProviderEntry.in_managed_hosting

from robotsix_auto_mail.protocol import _ProtocolClient
_ProtocolClient._oauth2_client_id
_ProtocolClient._oauth2_client_secret

# ===========================================================================
# Pydantic model fields — accessed via model_dump / model_validate / keyword
# construction, never read as plain class attributes by application code.
# ===========================================================================

from robotsix_auto_mail.calendar.schema import CalendarEventRequest
CalendarEventRequest.correlation_id
CalendarEventRequest.body_text
CalendarEventRequest.email_date
CalendarEventRequest.extracted_dates
