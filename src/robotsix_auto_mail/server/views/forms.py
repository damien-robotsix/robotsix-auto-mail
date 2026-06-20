"""Form renderers for the board server."""

from __future__ import annotations

import html
from urllib.parse import quote

from robotsix_auto_mail.db import MailRecord
from robotsix_auto_mail.triage import TRIAGE_ACTION_LABELS, TRIAGE_ACTION_ORDER


def _render_move_form(
    record: MailRecord,
    current_action: str,
    redirect_input: str,
    *,
    account_id: str | None = None,
    calendar_enabled: bool = True,
) -> str:
    """Render the Status ``<option>`` list and the ``/move`` form.

    When *account_id* is a real account (not ``None`` and not the
    aggregate sentinel), ``?account=<id>`` is appended to the form
    ``action`` so the POST routes to the correct account's database.

    When *calendar_enabled* is ``False``, ``TO_CALENDAR`` is omitted
    from the status options.
    """
    options_parts: list[str] = []
    for action in TRIAGE_ACTION_ORDER:
        if action == "TO_CALENDAR" and not calendar_enabled:
            continue
        sel = " selected" if action == current_action else ""
        options_parts.append(
            f'<option value="{html.escape(action)}"{sel}>'
            f"{html.escape(TRIAGE_ACTION_LABELS[action])}</option>"
        )
    account_qs = ""
    if account_id is not None and account_id != "__all__":
        account_qs = "?account=" + quote(account_id, safe="")
    return (
        f'<form class="detail-form" method="post" action="/move{account_qs}">'
        f'<input type="hidden" name="message_id"'
        f' value="{html.escape(record.message_id)}">'
        f"{redirect_input}"
        f'<select name="triage_action">{"".join(options_parts)}</select>'
        '<button type="submit">Move</button>'
        "</form>"
    )
