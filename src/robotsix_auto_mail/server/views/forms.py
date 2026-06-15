"""Form renderers for the board server."""

from __future__ import annotations

import html

from robotsix_auto_mail.db import MailRecord
from robotsix_auto_mail.triage import TRIAGE_ACTION_LABELS, TRIAGE_ACTION_ORDER


def _render_move_form(
    record: MailRecord, current_action: str, redirect_input: str
) -> str:
    """Render the Status ``<option>`` list and the ``/move`` form."""
    options_parts: list[str] = []
    for action in TRIAGE_ACTION_ORDER:
        sel = " selected" if action == current_action else ""
        options_parts.append(
            f'<option value="{html.escape(action)}"{sel}>'
            f"{html.escape(TRIAGE_ACTION_LABELS[action])}</option>"
        )
    return (
        '<form class="detail-form" method="post" action="/move">'
        f'<input type="hidden" name="message_id"'
        f' value="{html.escape(record.message_id)}">'
        f"{redirect_input}"
        f'<select name="triage_action">{"".join(options_parts)}</select>'
        '<button type="submit">Move</button>'
        "</form>"
    )
