"""Compose-draft mixin for the board server — POST /compose-draft.

Creates a NEW outgoing draft (not a reply) with optional file-hub
attachments, stores it in the board's Draft-ready column, and appends
the MIME message into the account's real IMAP Drafts folder.
"""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import io
import json
import logging
import uuid
from datetime import UTC, datetime
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
            try:
                for fid in attachment_ids:
                    if not isinstance(fid, str) or not fid:
                        self._bad_request(
                            "Each attachment ID must be a non-empty string"
                        )
                        return
                    # Use the metadata endpoint, NOT the raw-file-download
                    # endpoint (/files/<id>), which returns binary content.
                    fetch_url = f"{file_hub_url.rstrip('/')}/files/{fid}/metadata"
                    try:
                        with httpx.Client(timeout=30) as client:
                            resp = client.get(
                                fetch_url,
                                headers={"Accept": "application/json"},
                            )
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

                    try:
                        meta = resp.json()
                    except Exception:
                        logger.warning(
                            "file-hub returned non-JSON response for "
                            "attachment %s (status %d)",
                            fid,
                            resp.status_code,
                        )
                        self._send_response(
                            json.dumps(
                                {
                                    "error": (
                                        f"file-hub returned non-JSON "
                                        f"response for attachment {fid}"
                                    )
                                }
                            ),
                            status=502,
                            content_type="application/json; charset=utf-8",
                        )
                        return
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
            except Exception:
                logger.exception("Unexpected error during attachment validation")
                self._send_response(
                    json.dumps({"error": "Attachment validation failed"}),
                    status=502,
                    content_type="application/json; charset=utf-8",
                )
                return

        # -- create the mail record ----------------------------------------
        message_id = f"<compose-{uuid.uuid4().hex}@robotsix-auto-mail>"
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

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

        # -- IMAP APPEND into Drafts folder -------------------------------
        self._append_to_drafts_folder(
            account=account,
            from_addr=from_addr,
            to_addr=to_addr,
            subject=subject,
            body=draft_body,
            attachment_ids=attachment_ids,
            attachments_meta=attachments_meta,
            file_hub_url=file_hub_url,
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

    def _append_to_drafts_folder(
        self,
        *,
        account: Any,
        from_addr: str,
        to_addr: str,
        subject: str,
        body: str,
        attachment_ids: list[str],
        attachments_meta: list[dict[str, Any]],
        file_hub_url: str,
    ) -> None:
        """Build a MIME message and APPEND it to the account's Drafts folder.

        Downloads attachment content from file-hub, constructs a multipart
        MIME message, discovers the Drafts mailbox, and appends with the
        ``\\Draft`` flag.  Errors are logged but do not fail the request —
        the board card is the fallback.
        """
        from robotsix_auto_mail.imap import ImapClient
        from robotsix_auto_mail.imap.mailbox import is_special_use
        from robotsix_auto_mail.mime import build_multipart_message

        attachment_files: list[io.BytesIO] = []
        attachment_names: list[str] = []
        try:
            # -- download attachment content from file-hub -----------------
            if attachment_ids and file_hub_url:
                for fid in attachment_ids:
                    download_url = (
                        f"{file_hub_url.rstrip('/')}/files/{fid}"
                    )
                    try:
                        with httpx.Client(timeout=60) as http_client:
                            resp = http_client.get(download_url)
                    except Exception as exc:
                        logger.warning(
                            "Failed to download attachment %s: %s", fid, exc
                        )
                        return
                    if resp.status_code >= 400:
                        logger.warning(
                            "file-hub returned %d for attachment %s",
                            resp.status_code,
                            fid,
                        )
                        return
                    attachment_files.append(io.BytesIO(resp.content))
                    # Find the matching filename from metadata
                    fname = "attachment"
                    for meta in attachments_meta:
                        if meta.get("file_hub_id") == fid:
                            fname = meta.get("filename", "attachment")
                            break
                    attachment_names.append(fname)

            # -- build MIME message ----------------------------------------
            msg = build_multipart_message(
                from_addr=from_addr,
                to_addr=to_addr,
                subject=subject,
                body=body,
                attachments=attachment_files,
                attachment_names=attachment_names,
            )
            msg_bytes = msg.as_bytes()

            # -- discover Drafts folder and APPEND -------------------------
            with ImapClient(account.config) as imap:
                folders = imap.list_folders()
                drafts_folder: str | None = None
                for folder_info in folders:
                    if any(
                        attr.lower() == "\\drafts"
                        for attr in folder_info.attributes
                    ):
                        drafts_folder = folder_info.name
                        break
                if drafts_folder is None:
                    for folder_info in folders:
                        if "draft" in folder_info.name.lower():
                            drafts_folder = folder_info.name
                            break
                if drafts_folder is None:
                    logger.warning(
                        "No Drafts folder found on the server; "
                        "skipping IMAP APPEND"
                    )
                    return

                imap.append_message(
                    drafts_folder,
                    msg_bytes,
                    flags="(\\Draft)",
                )
                logger.info(
                    "Appended compose-draft to %s/%s with \\Draft flag",
                    account.config.username,
                    drafts_folder,
                )
        except Exception:
            logger.exception(
                "Failed to IMAP-APPEND compose-draft to Drafts folder"
            )
        finally:
            for f in attachment_files:
                f.close()
