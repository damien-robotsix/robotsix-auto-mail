"""Compose-draft mixin for the board server — POST /compose-draft.

Creates a NEW outgoing draft (not a reply) with optional file-hub
attachments and stores it in the board's Draft-ready column.
"""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx

from robotsix_auto_mail.server._constants import _with_db
from robotsix_auto_mail.triage import (
    DRAFT_READY,
    set_triage_decision,
)

logger = logging.getLogger(__name__)


class _ComposeDraftMixin:
    """Mixin providing POST /compose-draft for creating new outgoing drafts."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    def _handle_compose_draft(self) -> None:
        """Process POST /compose-draft — create a new outgoing draft.

        JSON request body::

            {
                "account": "<account_id>",
                "to": "recipient@example.com",
                "subject": "Subject line",
                "body": "Draft body text",
                "attachments": ["file-hub-id-1", "file-hub-id-2"]
            }

        ``account`` is required.  ``to``, ``subject``, and ``body`` are
        required strings.  ``attachments`` is an optional list of file-hub
        file IDs; each is validated against the file-hub service.

        The draft is stored as a new ``mail_records`` row with triage
        decision ``DRAFT_READY`` so it appears in the Draft-ready column
        on the board.

        Errors:
        - 400: missing/invalid fields
        - 404: unknown account or unknown file-hub attachment id
        - 502: file-hub unreachable
        """
        from robotsix_auto_mail.db import insert_record
        from robotsix_auto_mail.db.models import MailRecord

        # -- parse JSON body -----------------------------------------------
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = (
            self.rfile.read(content_length).decode("utf-8") if content_length else ""
        )
        try:
            body: dict[str, Any] = json.loads(raw_body) if raw_body.strip() else {}
        except json.JSONDecodeError:
            self._bad_request("Malformed JSON body")
            return
        if not isinstance(body, dict):
            self._bad_request("Request body must be a JSON object")
            return

        account_id = body.get("account", "")
        to_addr = body.get("to", "")
        subject = body.get("subject", "")
        draft_body = body.get("body", "")
        attachment_ids = body.get("attachments", [])

        # -- validate required fields --------------------------------------
        if not account_id:
            self._bad_request("Missing required field: account")
            return
        if not to_addr:
            self._bad_request("Missing required field: to")
            return
        if not subject:
            self._bad_request("Missing required field: subject")
            return
        if not draft_body:
            self._bad_request("Missing required field: body")
            return
        if not isinstance(attachment_ids, list):
            self._bad_request("attachments must be a list of file-hub IDs")
            return

        # -- resolve account -----------------------------------------------
        accounts = self.accounts
        if accounts is None:
            self._bad_request("No accounts configured")
            return
        try:
            account = accounts.get(account_id)
        except Exception:
            self._send_response(
                json.dumps({"error": f"Unknown account: {account_id}"}),
                status=404,
                content_type="application/json; charset=utf-8",
            )
            return

        db_path = account.config.db_path
        from_addr = account.config.username

        # -- validate file-hub is configured (only if attachments given) ---
        file_hub_url: str = getattr(accounts, "file_hub_url", "") or ""
        attachments_meta: list[dict[str, Any]] = []

        if attachment_ids:
            if not file_hub_url:
                self._send_response(
                    "file-hub is not configured (set file_hub_url in config)",
                    status=503,
                )
                return

            # -- fetch each attachment from file-hub -----------------------
            for fid in attachment_ids:
                if not isinstance(fid, str) or not fid:
                    self._bad_request(
                        "Each attachment ID must be a non-empty string"
                    )
                    return
                fetch_url = f"{file_hub_url.rstrip('/')}/files/{fid}"
                try:
                    with httpx.Client(timeout=30) as client:
                        resp = client.get(fetch_url)
                except Exception as exc:
                    logger.warning("file-hub unreachable: %s", exc)
                    self._send_response(
                        json.dumps({"error": "file-hub unreachable"}),
                        status=502,
                        content_type="application/json; charset=utf-8",
                    )
                    return

                if resp.status_code == 404:
                    self._send_response(
                        json.dumps(
                            {"error": f"Unknown file-hub attachment: {fid}"}
                        ),
                        status=404,
                        content_type="application/json; charset=utf-8",
                    )
                    return
                if resp.status_code >= 400:
                    self._send_response(
                        json.dumps(
                            {
                                "error": (
                                    f"file-hub returned {resp.status_code} "
                                    f"for attachment {fid}"
                                )
                            }
                        ),
                        status=502,
                        content_type="application/json; charset=utf-8",
                    )
                    return

                meta = resp.json()
                attachments_meta.append(
                    {
                        "file_hub_id": fid,
                        "filename": meta.get("filename", "attachment"),
                        "mime_type": meta.get(
                            "content_type", "application/octet-stream"
                        ),
                        "size": meta.get("size", 0),
                    }
                )

        # -- create the mail record ----------------------------------------
        message_id = f"<compose-{uuid.uuid4().hex}@robotsix-auto-mail>"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        record = MailRecord(
            message_id=message_id,
            sender=from_addr,
            subject=subject,
            date=now,
            recipients_json=json.dumps({"to": [to_addr], "cc": []}),
            body_plain=draft_body,
            body_html="",
            attachments_json=json.dumps(attachments_meta),
            draft_text=draft_body,
            status="to_read",
        )

        with _with_db(db_path) as conn:
            insert_record(conn, record)
            set_triage_decision(
                conn,
                message_id,
                DRAFT_READY,
                source="user",
                reason="compose-draft",
            )

        # -- respond -------------------------------------------------------
        self._serve_json(
            {
                "message_id": message_id,
                "account": account_id,
                "to": to_addr,
                "subject": subject,
                "attachments": len(attachments_meta),
            },
            status=201,
        )
