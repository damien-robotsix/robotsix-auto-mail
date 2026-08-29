"""Compose-draft mixin for the board server — POST /compose-draft.

Builds a fully-formed RFC822 message (reply or new) with correct From /
To / Cc / Subject, threading headers for replies, body, and all file-hub
attachments fetched and included as MIME parts, then ``APPEND``\\ s it
directly into the target account's real IMAP Drafts folder.  The draft
then appears natively in the user's mail client (Gmail / Roundcube) for
manual review and send.

The board does **not** store draft records and does **not** send mail —
composing lands the message in the mailbox Drafts folder and nothing
more.  If a file-hub attachment cannot be fetched the request fails
loudly; a stripped draft is never written.
"""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING, Any, BinaryIO

import httpx

from robotsix_auto_mail.server._constants import _with_db

logger = logging.getLogger(__name__)


def _compute_reply_all_cc(
    recipients_json: str, from_addr: str, sender: str
) -> list[str] | None:
    """Compute the Cc list for a reply-all, excluding self and the sender."""
    try:
        recipients = json.loads(recipients_json)
    except json.JSONDecodeError, TypeError:
        recipients = {}
    orig_to = recipients.get("to", []) if isinstance(recipients, dict) else []
    orig_cc = recipients.get("cc", []) if isinstance(recipients, dict) else []
    cc_list: list[str] = []
    seen: set[str] = set()
    excluded = {from_addr.lower(), sender.lower()}
    for addr in [*orig_to, *orig_cc]:
        if not isinstance(addr, str):
            continue
        lowered = addr.lower()
        if lowered in excluded or lowered in seen:
            continue
        seen.add(lowered)
        cc_list.append(addr)
    return cc_list or None


class _ComposeDraftMixin:
    """Mixin providing POST /compose-draft — compose directly to IMAP Drafts."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    def _handle_compose_draft(self) -> None:
        """Process POST /compose-draft — compose a message into IMAP Drafts.

        JSON request body::

            {
                "account": "<account_id>",
                "to": "recipient@example.com",
                "subject": "Subject line",
                "body": "Message body text",
                "attachments": ["file-hub-id-1", "file-hub-id-2"],
                "reply_to_message_id": "<orig@example.com>",
                "reply_all": false
            }

        ``account`` and ``body`` are always required.  For a **new**
        message ``to`` and ``subject`` are required.  For a **reply**
        (``reply_to_message_id`` set) ``to``/``subject`` default to the
        original sender and a ``Re:`` subject, and threading headers
        (``In-Reply-To`` / ``References``) are set; ``reply_all`` adds the
        original recipients as Cc.

        The composed RFC822 message — including every file-hub attachment
        as a MIME part — is ``APPEND``\\ ed to the account's Drafts folder.
        The board stores nothing; sending is done manually from the user's
        mail client.

        Errors:
        - 400: missing/invalid fields
        - 404: unknown account / file-hub attachment / reply target
        - 502: file-hub unreachable or IMAP APPEND failed
        - 503: file-hub not configured but attachments requested
        """
        from robotsix_auto_mail.db import get_record_by_message_id

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
        reply_to_message_id = body.get("reply_to_message_id", "")
        reply_all = bool(body.get("reply_all", False))

        # -- validate always-required fields -------------------------------
        if not account_id:
            self._bad_request("Missing required field: account")
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

        # -- reply threading: derive To/Cc/Subject from the original -------
        in_reply_to: str | None = None
        references: str | None = None
        cc: list[str] | None = None
        if reply_to_message_id:
            with _with_db(db_path) as conn:
                original = get_record_by_message_id(conn, reply_to_message_id)
            if original is None:
                self._send_response(
                    json.dumps(
                        {"error": f"Unknown reply target: {reply_to_message_id}"}
                    ),
                    status=404,
                    content_type="application/json; charset=utf-8",
                )
                return
            if not to_addr:
                to_addr = original.sender
            if not subject:
                subject = (
                    original.subject
                    if original.subject.lower().startswith("re:")
                    else f"Re: {original.subject}"
                )
            if reply_all:
                cc = _compute_reply_all_cc(
                    original.recipients_json, from_addr, original.sender
                )
            in_reply_to = reply_to_message_id
            references = reply_to_message_id

        # -- validate derived required fields ------------------------------
        if not to_addr:
            self._bad_request("Missing required field: to")
            return
        if not subject:
            self._bad_request("Missing required field: subject")
            return

        # -- fetch attachments (fail loudly — never write a stripped draft)-
        file_hub_url: str = getattr(accounts, "file_hub_url", "") or ""
        attachment_files: list[BinaryIO] = []
        attachment_names: list[str] = []

        if attachment_ids:
            if not file_hub_url:
                self._send_response(
                    "file-hub is not configured (set file_hub_url in config)",
                    status=503,
                )
                return
            error = self._fetch_attachments(
                attachment_ids, file_hub_url, attachment_files, attachment_names
            )
            if error is not None:
                for f in attachment_files:
                    f.close()
                status, message = error
                self._send_response(
                    json.dumps({"error": message}),
                    status=status,
                    content_type="application/json; charset=utf-8",
                )
                return

        # -- build the RFC822 message and APPEND to Drafts -----------------
        try:
            drafts_folder = self._append_to_drafts_folder(
                account=account,
                from_addr=from_addr,
                to_addr=to_addr,
                subject=subject,
                body=draft_body,
                cc=cc,
                in_reply_to=in_reply_to,
                references=references,
                attachment_files=attachment_files,
                attachment_names=attachment_names,
            )
        except Exception as exc:
            logger.exception("Failed to IMAP-APPEND compose-draft to Drafts folder")
            self._send_response(
                json.dumps({"error": f"IMAP APPEND failed: {exc}"}),
                status=502,
                content_type="application/json; charset=utf-8",
            )
            return
        finally:
            for f in attachment_files:
                f.close()

        # -- record the compose→card linkage for auto-archive-on-send ------
        # Only replies map to an existing board card; a new message has no
        # card to archive, so nothing is recorded for it.  A later reconcile
        # cycle detects the sent reply in the Sent folder and archives the
        # card (whether it was sent from the board or externally).
        if reply_to_message_id:
            from robotsix_auto_mail.db import record_compose_link

            with _with_db(db_path) as conn:
                record_compose_link(
                    conn,
                    reply_to_message_id,
                    subject=subject,
                    to_addr=to_addr,
                )

        # -- respond -------------------------------------------------------
        self._serve_json(
            {
                "account": account_id,
                "to": to_addr,
                "subject": subject,
                "drafts_folder": drafts_folder,
                "attachments": len(attachment_names),
                "reply": bool(reply_to_message_id),
            },
            status=201,
        )

    def _fetch_attachments(
        self,
        attachment_ids: list[Any],
        file_hub_url: str,
        attachment_files: list[BinaryIO],
        attachment_names: list[str],
    ) -> tuple[int, str] | None:
        """Fetch each attachment's metadata + content from the file-hub.

        Populates *attachment_files* and *attachment_names* in place.
        Returns ``None`` on success, or ``(status, message)`` describing
        the failure — the caller must abort and never write a stripped
        draft.
        """
        base = file_hub_url.rstrip("/")
        for fid in attachment_ids:
            if not isinstance(fid, str) or not fid:
                return (400, "Each attachment ID must be a non-empty string")

            # -- metadata (filename) -------------------------------------
            try:
                with httpx.Client(timeout=30) as client:
                    meta_resp = client.get(
                        f"{base}/files/{fid}/metadata",
                        headers={"Accept": "application/json"},
                    )
            except Exception as exc:
                logger.warning("file-hub unreachable: %s", exc)
                return (502, "file-hub unreachable")
            if meta_resp.status_code == 404:
                return (404, f"Unknown file-hub attachment: {fid}")
            if meta_resp.status_code >= 400:
                return (
                    502,
                    f"file-hub returned {meta_resp.status_code} for attachment {fid}",
                )
            try:
                meta = meta_resp.json()
            except Exception:
                return (
                    502,
                    f"file-hub returned non-JSON response for attachment {fid}",
                )
            filename = meta.get("filename", "attachment")

            # -- content -------------------------------------------------
            try:
                with httpx.Client(timeout=60) as client:
                    content_resp = client.get(f"{base}/files/{fid}")
            except Exception as exc:
                logger.warning("Failed to download attachment %s: %s", fid, exc)
                return (502, f"Failed to download attachment {fid}")
            if content_resp.status_code == 404:
                return (404, f"Unknown file-hub attachment: {fid}")
            if content_resp.status_code >= 400:
                return (
                    502,
                    f"file-hub returned {content_resp.status_code} "
                    f"for attachment {fid}",
                )

            attachment_files.append(io.BytesIO(content_resp.content))
            attachment_names.append(filename)
        return None

    def _append_to_drafts_folder(
        self,
        *,
        account: Any,
        from_addr: str,
        to_addr: str,
        subject: str,
        body: str,
        cc: list[str] | None,
        in_reply_to: str | None,
        references: str | None,
        attachment_files: list[BinaryIO],
        attachment_names: list[str],
    ) -> str:
        """Build a MIME message and APPEND it to the account's Drafts folder.

        Returns the name of the Drafts folder the message was appended to.
        Raises on any failure (no Drafts folder, APPEND error) so the
        caller can fail loudly — a stripped or lost draft is never
        silently accepted.
        """
        from robotsix_auto_mail.imap import ImapClient
        from robotsix_auto_mail.mime import build_multipart_message

        msg = build_multipart_message(
            from_addr=from_addr,
            to_addr=to_addr,
            subject=subject,
            body=body,
            attachments=attachment_files,
            attachment_names=attachment_names,
            cc=cc,
            in_reply_to=in_reply_to,
            references=references,
        )
        msg_bytes = msg.as_bytes()

        with ImapClient(account.config) as imap:
            folders = imap.list_folders()
            drafts_folder: str | None = None
            for folder_info in folders:
                if any(attr.lower() == "\\drafts" for attr in folder_info.attributes):
                    drafts_folder = folder_info.name
                    break
            if drafts_folder is None:
                for folder_info in folders:
                    if "draft" in folder_info.name.lower():
                        drafts_folder = folder_info.name
                        break
            if drafts_folder is None:
                raise RuntimeError("No Drafts folder found on the IMAP server")

            imap.append_message(drafts_folder, msg_bytes, flags="(\\Draft)")
            logger.info(
                "Appended compose-draft to %s/%s with \\Draft flag",
                account.config.username,
                drafts_folder,
            )
            return drafts_folder
