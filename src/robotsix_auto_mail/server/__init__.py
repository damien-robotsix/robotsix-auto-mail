"""HTTP server for the read-only kanban mail board.

Provides ``make_board_handler`` — a factory that returns a
``BaseHTTPRequestHandler`` subclass wired to a specific SQLite database
path.

The implementation is split across internal submodules:

- ``_constants`` — static assets, board columns, and leaf helpers.
- ``adapters`` — the ``render_board`` column adapter and the
  background triage runner.
- ``views`` — HTML/view renderers.
- ``handlers`` — the ``BoardHandler`` request handler and the
  ``make_board_handler`` factory.

This module re-exports the public and previously-importable symbols so
``from robotsix_auto_mail.server import ...`` keeps working unchanged.
"""

from __future__ import annotations

from robotsix_auto_mail.server._constants import (
    _BOARD_COLUMNS,
    _STATIC_AUTOMAIL_BOARD_CSS,
    _STATIC_BOARD_CSS,
    _STATIC_BOARD_JS,
    _is_safe_redirect_path,
    _parse_archive_structure,
)
from robotsix_auto_mail.server.adapters import (
    _NonEmptyColumnsAdapter,
    _run_triage_background,
)
from robotsix_auto_mail.server.handlers import BoardHandler, make_board_handler
from robotsix_auto_mail.server.views import (
    _build_board_content,
    _build_board_html,
    _build_detail_html,
    _render_attachments,
    _render_board_columns,
    _render_body,
    _render_draft_section,
    _render_imap_uid_section,
    _render_move_form,
    _render_notes_section,
    _render_recipients,
    _render_rule_card,
    _render_triage_section,
)

__all__ = [
    "_BOARD_COLUMNS",
    "_STATIC_AUTOMAIL_BOARD_CSS",
    "_STATIC_BOARD_CSS",
    "_STATIC_BOARD_JS",
    "BoardHandler",
    "_NonEmptyColumnsAdapter",
    "_build_board_content",
    "_build_board_html",
    "_build_detail_html",
    "_is_safe_redirect_path",
    "_parse_archive_structure",
    "_render_attachments",
    "_render_board_columns",
    "_render_body",
    "_render_draft_section",
    "_render_imap_uid_section",
    "_render_move_form",
    "_render_notes_section",
    "_render_recipients",
    "_render_rule_card",
    "_render_triage_section",
    "_run_triage_background",
    "make_board_handler",
]
