"""HTML/view renderers for the board server.

Split across focused submodules:
- ``board`` — board rendering functions
- ``detail`` — email detail rendering functions
- ``forms`` — form renderers
"""

from __future__ import annotations

from robotsix_auto_mail.server.views.board import (
    _batch_banner_html as _batch_banner_html,
)
from robotsix_auto_mail.server.views.board import (
    _build_board_content as _build_board_content,
)
from robotsix_auto_mail.server.views.board import (
    _build_board_html as _build_board_html,
)
from robotsix_auto_mail.server.views.board import (
    _build_global_board_content as _build_global_board_content,
)
from robotsix_auto_mail.server.views.board import (
    _build_global_board_html as _build_global_board_html,
)
from robotsix_auto_mail.server.views.board import (
    _gather_account_board_data as _gather_account_board_data,
)
from robotsix_auto_mail.server.views.board import (
    _render_board_columns as _render_board_columns,
)
from robotsix_auto_mail.server.views.board import (
    _render_board_page_shell as _render_board_page_shell,
)
from robotsix_auto_mail.server.views.detail import (
    _build_detail_html as _build_detail_html,
)
from robotsix_auto_mail.server.views.detail import (
    _render_attachments as _render_attachments,
)
from robotsix_auto_mail.server.views.detail import (
    _render_body as _render_body,
)
from robotsix_auto_mail.server.views.detail import (
    _render_calendar_feedback as _render_calendar_feedback,
)
from robotsix_auto_mail.server.views.detail import (
    _render_draft_section as _render_draft_section,
)
from robotsix_auto_mail.server.views.detail import (
    _render_imap_uid_section as _render_imap_uid_section,
)
from robotsix_auto_mail.server.views.detail import (
    _render_notes_section as _render_notes_section,
)
from robotsix_auto_mail.server.views.detail import (
    _render_recipients as _render_recipients,
)
from robotsix_auto_mail.server.views.detail import (
    _render_triage_section as _render_triage_section,
)
from robotsix_auto_mail.server.views.forms import (
    _render_move_form as _render_move_form,
)

__all__ = [
    "_batch_banner_html",
    "_build_board_content",
    "_build_board_html",
    "_build_detail_html",
    "_build_global_board_content",
    "_build_global_board_html",
    "_gather_account_board_data",
    "_render_attachments",
    "_render_board_columns",
    "_render_board_page_shell",
    "_render_body",
    "_render_calendar_feedback",
    "_render_draft_section",
    "_render_imap_uid_section",
    "_render_move_form",
    "_render_notes_section",
    "_render_recipients",
    "_render_triage_section",
]
