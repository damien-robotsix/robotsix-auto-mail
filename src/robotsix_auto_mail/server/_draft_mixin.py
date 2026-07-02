"""Draft-handler mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from robotsix_auto_mail.db import MailRecord
from robotsix_auto_mail.server._constants import _is_safe_redirect_path, _with_db
from robotsix_auto_mail.triage import (
    DRAFT_READY,
    get_triage_decision,
    record_user_action,
    set_triage_decision,
)


def _compute_reply_all_cc(record: MailRecord, from_addr: str) -> list[str] | None:
    """Compute the CC list for a reply-all, excluding self and the original sender."""
    try:
        recipients = json.loads(record.recipients_json)
    except json.JSONDecodeError, TypeError:
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
        from robotsix_auto_mail.db import update_draft_text

        def save_draft_action(
            conn: Any, record: MailRecord, redirect_to: str, draft_text: str
        ) -> bool:
            update_draft_text(conn, record.message_id, draft_text)

            current = get_triage_decision(conn, record.message_id)
            if current is None or current.action != DRAFT_READY:
                set_triage_decision(
                    conn,
                    record.message_id,
                    DRAFT_READY,
                    source="user",
                    reason="draft saved",
                )
                if self.mail_config is not None:
                    record_user_action(record, DRAFT_READY, config=self.mail_config)
            return True

        self._handle_post_action(
            "message_id",
            "draft_text",
            "redirect_to",
            no_strip=frozenset({"draft_text"}),
            action=save_draft_action,
        )

    def _handle_send_draft(self) -> None:
        """Process POST /send-draft — send the saved draft via SMTP, then
        re-queue the original message for triage.

        Mirrors :meth:`_handle_save_draft` for form parsing/validation.
        After a successful send the original record is **not** archived;
        instead its sent reply body is persisted and its triage decision is
        cleared so the email re-enters the untriaged pool and the triage
        agent owns the post-answer disposition.
        """
        from robotsix_auto_mail.db import update_sent_reply_text
        from robotsix_auto_mail.triage import delete_triage_decision

        def send_draft_action(
            conn: Any,
            record: MailRecord,
            redirect_to: str,
            reply_mode: str,
            forward_to: str = "",
        ) -> bool:
            if reply_mode not in ("reply", "reply_all", "forward"):
                self._bad_request(f"Invalid reply_mode: {reply_mode!r}")
                return False

            # SMTP must be configured to send anything.
            if self.mail_config is None or not self.mail_config.smtp_host:
                self._bad_request("SMTP is not configured")
                return False
            mail_config = self.mail_config

            if not record.draft_text.strip():
                self._bad_request("Draft is empty; nothing to send")
                return False

            # -- compute recipients ------------------------------------
            from_addr = mail_config.username

            if reply_mode == "forward":
                if not forward_to.strip():
                    self._bad_request("forward_to is required for forward mode")
                    return False
                if forward_to.strip().lower() == from_addr.strip().lower():
                    self._bad_request("Refusing to forward to your own address")
                    return False
                to_addr = forward_to.strip()
                cc = None
            else:
                to_addr = record.sender

                # Defensive guard: never reply to the user's own address
                # (a self-sent message that slipped through triage).
                if to_addr.strip().lower() == from_addr.strip().lower():
                    self._bad_request("Refusing to send a reply to your own address")
                    return False

                cc = (
                    _compute_reply_all_cc(record, from_addr)
                    if reply_mode == "reply_all"
                    else None
                )

            # -- subject -----------------------------------------------
            subject = record.subject
            if reply_mode == "forward":
                if not subject.lower().startswith("fwd:"):
                    subject = f"Fwd: {subject}"
            else:
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
                    in_reply_to=(
                        None if reply_mode == "forward" else record.message_id
                    ),
                    references=(None if reply_mode == "forward" else record.message_id),
                )

            # -- re-queue for triage: persist the sent reply and drop the
            #    existing triage decision so the record re-enters the
            #    untriaged pool (no archive move, no record deletion) ----
            update_sent_reply_text(conn, record.message_id, record.draft_text)
            delete_triage_decision(conn, record.message_id)
            return True

        self._handle_post_action(
            "message_id",
            "reply_mode",
            "forward_to",
            "redirect_to",
            action=send_draft_action,
        )

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
        fields = self._parse_request_body("message_id", "redirect_to")

        message_id = fields["message_id"]
        redirect_to = fields["redirect_to"]

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

        with _with_db(self.db_path) as conn:
            try:
                generate_draft_reply(
                    conn,
                    message_id,
                    api_key=(
                        self.mail_config.llm_api_key.get_secret_value()
                        if self.mail_config
                        else None
                    ),
                    provider_model=(
                        self.mail_config.llm_provider_model
                        if self.mail_config
                        else None
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
                    DRAFT_READY,
                    source="user",
                    reason="draft generated",
                )

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
