"""Board command handlers — extracted from commands.py."""

from __future__ import annotations

import sys
from typing import TextIO

from robotsix_auto_mail.cli.commands import _print_header
from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import MailRecord, init_db, list_records
from robotsix_auto_mail.format import (
    _BODY_PREVIEW_LIMIT,
    _effective_body_plain,
    _format_date,
)

_SEPARATOR = "-" * 60 + "\n"


def _render_card(record: MailRecord, file: TextIO) -> None:
    """Render a single mail record to *file*."""
    # Sender
    file.write(f"From:    {record.sender}\n")

    # Subject
    subject = record.subject if record.subject.strip() else "(no subject)"
    file.write(f"Subject: {subject}\n")

    # Date
    file.write(f"Date:    {_format_date(record.date)}\n")

    # Body preview
    body = _effective_body_plain(record)
    if not body or not body.strip():
        preview = "(no body)"
    elif len(body) > _BODY_PREVIEW_LIMIT:
        preview = body[:_BODY_PREVIEW_LIMIT] + "…"
    else:
        preview = body
    file.write(f"\n{preview}\n")


def _render_board(records: list[MailRecord], file: TextIO) -> None:
    """Render every *record* in the inbox board view to *file*."""
    if not records:
        file.write("Your inbox is empty.\n")
        return

    for i, record in enumerate(records):
        if i > 0:
            file.write(_SEPARATOR)
        _render_card(record, file)

    count = len(records)
    file.write(f"{count} message(s)\n")


def _cmd_board(config: MailConfig) -> int:
    """Run the board subcommand: display ingested mail in a read-only view.

    Returns 0 on success, 1 on failure to load configuration.
    """
    conn = init_db(config.db_path)
    try:
        records = list_records(conn)
    finally:
        conn.close()

    _print_header(sys.stdout, "Inbox")
    _render_board(records, sys.stdout)

    return 0
