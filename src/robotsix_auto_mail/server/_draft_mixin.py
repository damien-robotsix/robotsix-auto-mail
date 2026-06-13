"""Draft-handler mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, quote

from robotsix_auto_mail.db import MailRecord
from robotsix_auto_mail.server._constants import _is_safe_redirect_path
from robotsix_auto_mail.triage import (
    get_triage_decision,
    record_human_decision,
    set_triage_decision,
)


def _compute_reply_all_cc(record: MailRecord, from_addr: str) -> list[str] | None:
    """Compute the CC list for a reply-all, excluding self and the original sender."""
    try:
        recipients = json.loads(record.recipients_json)
    except (json.JSONDecodeError, TypeError):
        recipients = {}
    orig_to = recipients.get("to", []) if isinstance(recipients, dict) else []
    orig_cc = recipients.get("cc", []) if isinstance(recipients, dict) else []
    to_addr = record.sender
    cc_list: list[str] = []
    seen: set[str] = set()
    excluded = {from_addr.lower(), to_addr.lower()}
    for addr in [*orig_to, *orig_cc]:
        if not isinstance(addr, str):
            continue
        lowered = addr.lower()
        if lowered in excluded or lowered in seen:
            continue
        seen.add(lowered)
        cc_list.append(addr)
    return cc_list or None


class _DraftMixin:
    """Mixin providing draft-related handlers for the board server."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    def _handle_save_draft(self) -> None:
        """Process POST /save-draft — persist draft text and move to DRAFT_READY."""
        from robotsix_auto_mail.db import (
            get_record_by_message_id,
            init_db,
            update_draft_text,
        )

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()
        draft_text = (fields.get("draft_text") or [""])[0]
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

        if not message_id:
            self._bad_request("Missing message_id")
            return

        # Verify the record exists (read-only check).
        conn = init_db(self.db_path, skip_migrations=True)
        try:
            if get_record_by_message_id(conn, message_id) is None:
                self._not_found()
                return
        finally:
            conn.close()

        # Persist draft text and move to DRAFT_READY.
        conn = init_db(self.db_path)
        try:
            update_draft_text(conn, message_id, draft_text)

            current = get_triage_decision(conn, message_id)
            if current is None or current.action != "DRAFT_READY":
                set_triage_decision(
                    conn,
                    message_id,
                    "DRAFT_READY",
                    source="user",
                    reason="draft saved",
                )
                record_human_decision(conn, message_id, "DRAFT_READY")
        finally:
            conn.close()

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
            self._redirect("/board", code=302)

    def _handle_send_draft(self) -> None:
        """Process POST /send-draft — send the saved draft via SMTP, then
        re-queue the original message for triage.

        Mirrors :meth:`_handle_save_draft` for form parsing/validation.
        After a successful send the original record is **not** archived;
        instead its sent reply body is persisted and its triage decision is
        cleared so the email re-enters the untriaged pool and the triage
        agent owns the post-answer disposition.
        """
        from robotsix_auto_mail.db import (
            get_record_by_message_id,
            init_db,
            update_sent_reply_text,
        )
        from robotsix_auto_mail.triage import delete_triage_decision

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()
        reply_mode = (fields.get("reply_mode") or [""])[0].strip()
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

        if not message_id:
            self._bad_request("Missing message_id")
            return

        # Validate reply mode up-front (cheap, no DB access).
        if reply_mode not in ("reply", "reply_all"):
            self._bad_request(f"Invalid reply_mode: {reply_mode!r}")
            return

        # SMTP must be configured to send anything.
        if self.mail_config is None or not self.mail_config.smtp_host:
            self._bad_request("SMTP is not configured")
            return
        mail_config = self.mail_config

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._not_found()
                return

            if not record.draft_text.strip():
                self._bad_request("Draft is empty; nothing to send")
                return

            # -- compute recipients ------------------------------------
            from_addr = mail_config.username
            to_addr = record.sender

            # Defensive guard: never reply to the user's own address
            # (a self-sent message that slipped through triage).
            if to_addr.strip().lower() == from_addr.strip().lower():
                self._bad_request("Refusing to send a reply to your own address")
                return

            cc = (
                _compute_reply_all_cc(record, from_addr)
                if reply_mode == "reply_all"
                else None
            )

            # -- subject (prepend "Re: " unless already present) -------
            subject = record.subject
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"

            # -- send via SMTP -----------------------------------------
            from robotsix_auto_mail.smtp import SmtpClient

            with SmtpClient(mail_config) as client:
                client.send(
                    from_addr=from_addr,
                    to_addr=to_addr,
                    subject=subject,
                    body=record.draft_text,
                    cc=cc,
                    in_reply_to=record.message_id,
                    references=record.message_id,
                )

            # -- re-queue for triage: persist the sent reply and drop the
            #    existing triage decision so the record re-enters the
            #    untriaged pool (no archive move, no record deletion) ----
            update_sent_reply_text(conn, record.message_id, record.draft_text)
            delete_triage_decision(conn, record.message_id)
        finally:
            conn.close()

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
            self._redirect("/board", code=302)

    def _handle_generate_draft(self) -> None:
        """Process POST /generate-draft — LLM-generate a draft reply.

        Lazily imports the optional LLM-backed draft generator so the rest
        of the server works without ``pydantic_ai`` installed.  On a missing
        optional extra (``ImportError``) the handler degrades gracefully by
        redirecting back to the detail/board view (the manual textarea stays
        usable) rather than returning a 503 — a full-page POST cannot render
        a clean JSON error.  Generation failures are likewise swallowed so
        the existing draft/manual form remains available.
        """
        from robotsix_auto_mail.db import init_db

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

        if not message_id:
            self._bad_request("Missing message_id")
            return

        try:
            from robotsix_auto_mail.draft import (
                DraftGenerationError,
                generate_draft_reply,
            )
        except ImportError:
            # Optional LLM extra not installed — degrade silently.
            self._redirect_generate_draft(message_id, redirect_to)
            return

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            try:
                generate_draft_reply(
                    conn,
                    message_id,
                    api_key=(
                        self.mail_config.llm_api_key if self.mail_config else None
                    ),
                    provider=(
                        self.mail_config.llm_provider if self.mail_config else None
                    ),
                )
            except DraftGenerationError:
                # Generation failed — degrade gracefully (existing draft /
                # manual form stays); fall through to the redirect.
                pass
            else:
                set_triage_decision(
                    conn,
                    message_id,
                    "DRAFT_READY",
                    source="user",
                    reason="draft generated",
                )
        finally:
            conn.close()

        self._redirect_generate_draft(message_id, redirect_to)

    def _redirect_generate_draft(self, message_id: str, redirect_to: str) -> None:
        """Redirect after /generate-draft to *redirect_to* or the board panel.

        When *redirect_to* is a safe relative path it is used (returning the
        iframe to the embed detail view).  Otherwise a server-side trusted
        ``/board#{message_id}`` redirect re-opens the side panel on the now
        ``DRAFT_READY`` card.
        """
        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, 302)
        else:
            self._redirect(f"/board#{quote(message_id)}", 302)
