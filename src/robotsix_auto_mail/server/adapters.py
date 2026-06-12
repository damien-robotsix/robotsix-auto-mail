"""Adapter and background-task helpers for the board server."""

from __future__ import annotations

from typing import Any

from robotsix_auto_mail.board_adapter import MailBoardAdapter
from robotsix_auto_mail.config import MailConfig


class _NonEmptyColumnsAdapter:
    """Adapter view exposing only the populated columns to ``render_board``.

    auto-mail hides empty columns, but ``render_board`` renders one column
    per :meth:`MailBoardAdapter.columns` entry.  This thin wrapper scopes
    ``columns()`` to *status_keys* (the non-empty columns, in board order)
    and delegates every other attribute — the ``card_*`` scaffold methods,
    ``move_endpoint`` and the ``card_extra_html`` / ``column_extra_html``
    raw-HTML hooks — to the wrapped :class:`MailBoardAdapter`.
    """

    def __init__(self, adapter: MailBoardAdapter, status_keys: list[str]) -> None:
        self._adapter = adapter
        self._status_keys = status_keys

    def columns(self) -> list[tuple[str, str]]:
        labels = dict(self._adapter.columns())
        return [(key, labels[key]) for key in self._status_keys]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)


def _run_triage_background(db_path: str, user_email: str | None = None) -> None:
    """Run the triage agent in a background thread, clearing the watermark on exit.

    Opens its own SQLite connection so it never shares a connection with
    the HTTP request-serve thread.  After triaging, derives fresh
    deterministic rule proposals from the updated triage history (no LLM)
    and records the genuinely-new ones as ``pending`` so the board can
    surface them for human validation.  The ``triage_run:state`` watermark
    is always set back to ``"idle"`` in a ``finally`` block — even when the
    triage module cannot be imported or ``run_triage_agent`` raises.
    """
    from robotsix_auto_mail.db import init_db, set_watermark

    conn = init_db(db_path, skip_migrations=True)
    try:
        try:
            from robotsix_auto_mail.triage import (
                propose_triage_rules,
                record_and_filter_rule_proposals,
                run_triage_agent,
            )
        except ImportError:
            return
        run_triage_agent(conn, user_email=user_email)
        # Surface freshly-derived rule proposals on the board.  This is a
        # deterministic, LLM-free scan of triage history, so it is cheap to
        # run on every triage pass; record_and_filter only writes the
        # ledger when there is a genuinely-new proposal.
        record_and_filter_rule_proposals(conn, propose_triage_rules(conn))
    except Exception:  # noqa: S110  # nosec B110
        # Swallow all exceptions — the watermark is always cleared.
        pass
    finally:
        set_watermark(conn, "triage_run:state", "idle")
        conn.close()


def _run_folder_triage_background(
    db_path: str, mail_config: MailConfig, folder: str
) -> None:
    """Ingest a named IMAP folder then run the triage agent over it.

    Modelled on :func:`_run_triage_background`: opens its own SQLite
    connection (never shared with the request-serve thread), swallows all
    exceptions so a missing ``pydantic_ai``, an ``ImapError`` or a bad
    folder never wedges the board, and always sets the ``triage_run:state``
    watermark back to ``"idle"`` in a ``finally`` block.  ``ingest_folder``
    selects the explicit *folder*, searches ``ALL`` and dedups by
    Message-ID; it does not touch the INBOX watermark or create archive
    folders.
    """
    from robotsix_auto_mail.db import init_db, set_watermark

    conn = init_db(db_path, skip_migrations=True)
    try:
        try:
            from robotsix_auto_mail.triage import (
                propose_triage_rules,
                record_and_filter_rule_proposals,
                run_triage_agent,
            )
        except ImportError:
            return
        from robotsix_auto_mail.imap import ImapClient
        from robotsix_auto_mail.pipeline import ingest_folder

        with ImapClient(mail_config) as imap:
            ingest_folder(conn, imap, mail_config, folder)
        run_triage_agent(conn, user_email=mail_config.username)
        # Mirror the inbox helper's deterministic rule-proposal refresh.
        record_and_filter_rule_proposals(conn, propose_triage_rules(conn))
    except Exception:  # noqa: S110  # nosec B110
        # Swallow all exceptions — the watermark is always cleared.
        pass
    finally:
        set_watermark(conn, "triage_run:state", "idle")
        conn.close()
