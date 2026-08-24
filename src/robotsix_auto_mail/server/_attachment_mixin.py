"""Attachment-to-file-hub mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import email
import json
import logging
from typing import TYPE_CHECKING, Any

import httpx

from robotsix_auto_mail.server._constants import _with_db

logger = logging.getLogger(__name__)


class _AttachmentMixin:
    """Mixin providing POST /email/<id>/attachments/to-file-hub."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    def _handle_push_to_file_hub(self, message_id: str) -> None:
        """Push one or all attachments of a message to robotsix-file-hub.

        POST /email/<message_id>/attachments/to-file-hub

        Optional JSON body:
          {"filename": "invoice.pdf"}  — push a single attachment by name
          {"index": 0}                 — push a single attachment by index
          {} or omitted               — push all attachments

        Returns JSON with the file-hub upload results.
        """
        from robotsix_auto_mail.db import get_record_by_message_id

        # -- check file-hub is configured ----------------------------------
        accounts = self.accounts
        file_hub_url: str = ""
        if accounts is not None:
            file_hub_url = getattr(accounts, "file_hub_url", "") or ""
        if not file_hub_url:
            self._send_response(
                "file-hub is not configured (set file_hub_url in config)",
                status=503,
            )
            return

        # -- parse optional body -------------------------------------------
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = (
            self.rfile.read(content_length).decode("utf-8") if content_length else ""
        )
        selector: dict[str, Any] = {}
        if raw_body.strip():
            try:
                selector = json.loads(raw_body)
            except json.JSONDecodeError:
                self._bad_request("Malformed JSON body")
                return
            if not isinstance(selector, dict):
                self._bad_request("Request body must be a JSON object")
                return

        # -- look up the message -------------------------------------------
        with _with_db(self.db_path, skip_migrations=True) as conn:
            record = get_record_by_message_id(conn, message_id)
        if record is None:
            self._not_found()
            return

        # -- check attachments exist ---------------------------------------
        try:
            attachments_meta = json.loads(record.attachments_json)
        except json.JSONDecodeError, TypeError:
            attachments_meta = []
        if not isinstance(attachments_meta, list) or not attachments_meta:
            self._send_response(
                json.dumps({"error": "Message has no attachments"}),
                status=400,
                content_type="application/json; charset=utf-8",
            )
            return

        # -- resolve which attachments to push -----------------------------
        filename_filter = selector.get("filename")
        index_filter = selector.get("index")

        if filename_filter is not None:
            if not isinstance(filename_filter, str):
                self._bad_request("filename must be a string")
                return
            matching = [
                (i, a)
                for i, a in enumerate(attachments_meta)
                if isinstance(a, dict) and a.get("filename") == filename_filter
            ]
            if not matching:
                self._send_response(
                    json.dumps({"error": f"Attachment not found: {filename_filter}"}),
                    status=404,
                    content_type="application/json; charset=utf-8",
                )
                return
            selected_indices = {matching[0][0]}
        elif index_filter is not None:
            if not isinstance(index_filter, int) or index_filter < 0:
                self._bad_request("index must be a non-negative integer")
                return
            if index_filter >= len(attachments_meta):
                self._send_response(
                    json.dumps(
                        {
                            "error": (
                                f"Index {index_filter} out of range "
                                f"(message has {len(attachments_meta)} attachments)"
                            )
                        }
                    ),
                    status=400,
                    content_type="application/json; charset=utf-8",
                )
                return
            selected_indices = {index_filter}
        else:
            selected_indices = set(range(len(attachments_meta)))

        # -- fetch raw email from IMAP -------------------------------------
        if self.mail_config is None or record.imap_uid is None:
            self._send_response(
                "IMAP not available for this message",
                status=502,
            )
            return

        from robotsix_auto_mail.imap import ImapClient, ImapError

        try:
            with ImapClient(self.mail_config) as client:
                client.select_folder(record.source_folder)
                fetched = client.fetch_messages([record.imap_uid])
        except (ImapError, OSError) as exc:
            self._send_response(
                f"IMAP fetch failed: {exc}",
                status=502,
            )
            return

        if not fetched:
            self._send_response(
                "Message no longer exists on the mail server",
                status=502,
            )
            return

        _uid, raw_bytes = fetched[0]
        msg = email.message_from_bytes(raw_bytes)

        # -- extract attachment parts --------------------------------------
        attachment_parts: list[tuple[int, dict[str, Any], bytes]] = []
        att_idx = 0
        for part in msg.walk():
            if part.is_multipart():
                continue
            disposition = part.get_content_disposition()
            content_type = part.get_content_type()
            if disposition == "attachment" or (
                disposition != "inline"
                and content_type not in ("text/plain", "text/html")
            ):
                if att_idx in selected_indices and att_idx < len(attachments_meta):
                    payload = part.get_payload(decode=True)
                    if payload is None:
                        payload = b""
                    attachment_parts.append(
                        (att_idx, attachments_meta[att_idx], payload)
                    )
                att_idx += 1

        if not attachment_parts:
            self._send_response(
                json.dumps({"error": "No matching attachment parts found in MIME"}),
                status=502,
                content_type="application/json; charset=utf-8",
            )
            return

        # -- upload to file-hub --------------------------------------------
        upload_url = f"{file_hub_url.rstrip('/')}/files"
        results: list[dict[str, Any]] = []

        for _idx, meta, payload in attachment_parts:
            filename = meta.get("filename", "attachment")
            mime_type = meta.get("mime_type", "application/octet-stream")
            try:
                with httpx.Client(timeout=60) as client:
                    resp = client.post(
                        upload_url,
                        files={"file": (filename, payload, mime_type)},
                    )
            except Exception as exc:
                self._send_response(
                    f"file-hub upload failed: {exc}",
                    status=502,
                )
                return

            if resp.status_code >= 400:
                self._send_response(
                    f"file-hub returned {resp.status_code}: {resp.text}",
                    status=502,
                )
                return

            results.append(resp.json())

        self._send_response(
            json.dumps({"attachments": results}),
            status=200,
            content_type="application/json; charset=utf-8",
        )
