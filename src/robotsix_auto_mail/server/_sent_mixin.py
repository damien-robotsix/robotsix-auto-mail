"""Read-only Sent-folder access mixin for the board server.

Exposes each account's **Sent** folder over the chat HTTP API so an agent
can list outbound mail, read a single Sent message, and enumerate its
attachments.  Strictly read-only — no send, move, or delete happens here.
"""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

if TYPE_CHECKING:
    from robotsix_auto_mail.imap import ImapClient

logger = logging.getLogger(__name__)


class _SentMixin:
    """Mixin providing read-only ``GET /sent/messages`` and ``GET /sent/message``."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

        self: BoardHandlerProtocol

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _discover_sent_folder(client: ImapClient) -> str | None:
        """Return the IMAP Sent-folder name, or ``None`` when absent.

        Prefers the RFC 6154 ``\\Sent`` SPECIAL-USE attribute; falls back
        to the first folder whose name contains ``"sent"``
        (case-insensitive) for servers that do not advertise SPECIAL-USE.
        """
        folders = client.list_folders()
        for info in folders:
            if any(attr.lower() == "\\sent" for attr in info.attributes):
                return info.name
        for info in folders:
            if "sent" in info.name.lower():
                return info.name
        return None

    # -- GET /sent/messages ------------------------------------------------

    def _serve_sent_messages(self) -> None:
        """Serve GET /sent/messages — list messages in the account's Sent folder.

        Returns the same structured shape as ``/archive/<folder>/messages``:
        each message object carries ``uid``, ``subject``, ``from``, ``to``,
        ``date``, ``size``, ``flags``, and ``message_id``.

        Accepts optional ``?limit=N`` (default 500, max 2000) and
        ``?offset=N`` (default 0) query parameters.  Short-circuits in
        aggregate (``?account=__all__``) mode.  Read-only.
        """
        if self._aggregate:
            self._serve_json({"messages": [], "folder": ""})
            return

        if not self._require_imap_configured():
            return

        qs = parse_qs(urlsplit(self.path).query)
        try:
            limit = min(max(int(qs.get("limit", ["500"])[0]), 1), 2000)
        except (ValueError, TypeError):  # fmt: skip
            limit = 500
        try:
            offset = max(int(qs.get("offset", ["0"])[0]), 0)
        except (ValueError, TypeError):  # fmt: skip
            offset = 0

        from robotsix_auto_mail.imap import ImapClient, ImapError

        try:
            with ImapClient(self.mail_config) as client:
                sent_folder = self._discover_sent_folder(client)
                if sent_folder is None:
                    self._not_found()
                    return
                client.select_folder(sent_folder)
                all_uids = client.search_uids("ALL")
                # Newest first: IMAP UIDs are monotonically increasing.
                ordered = list(reversed(all_uids))
                uids = ordered[offset : offset + limit]
                envelopes = client.fetch_envelopes(uids)
        except ImapError as exc:
            self._send_response(
                f"IMAP error listing Sent folder: {exc}",
                status=502,
            )
            return
        except OSError as exc:
            self._send_response(
                f"IMAP connection error: {exc}",
                status=502,
            )
            return

        self._serve_json(
            {
                "folder": sent_folder,
                "total": len(all_uids),
                "shown": len(envelopes),
                "messages": envelopes,
            }
        )

    # -- GET /sent/message -------------------------------------------------

    def _serve_sent_message(self) -> None:
        """Serve GET /sent/message — read a single Sent message by UID.

        Requires ``?uid=<n>``.  Returns the message body/metadata plus an
        enumeration of its attachments::

            {"uid": N, "folder": "Sent", "subject": "…", "from": "…",
             "to": [...], "cc": [...], "date": "…",
             "body_plain": "…", "body_html": "…",
             "attachments": [{"filename": "…", "mime_type": "…", "size": N}]}

        Short-circuits in aggregate (``?account=__all__``) mode.  Read-only.
        """
        if self._aggregate:
            self._not_found()
            return

        if not self._require_imap_configured():
            return

        qs = parse_qs(urlsplit(self.path).query)
        uid_values = qs.get("uid")
        if not uid_values:
            self._bad_request("Missing required ?uid= query parameter")
            return
        try:
            uid = int(uid_values[0])
        except (ValueError, TypeError):  # fmt: skip
            self._bad_request("uid must be an integer")
            return

        from robotsix_auto_mail.imap import ImapClient, ImapError

        try:
            with ImapClient(self.mail_config) as client:
                sent_folder = self._discover_sent_folder(client)
                if sent_folder is None:
                    self._not_found()
                    return
                client.select_folder(sent_folder)
                fetched = client.fetch_messages([uid])
        except ImapError as exc:
            self._send_response(
                f"IMAP error reading Sent message: {exc}",
                status=502,
            )
            return
        except OSError as exc:
            self._send_response(
                f"IMAP connection error: {exc}",
                status=502,
            )
            return

        if not fetched:
            self._not_found()
            return

        _uid, raw_bytes = fetched[0]

        from robotsix_auto_mail.pipeline._parse import ParseError, parse_message

        try:
            record = parse_message(raw_bytes, imap_uid=uid, source_folder=sent_folder)
        except ParseError:
            self._send_response(
                "Failed to parse Sent message MIME",
                status=502,
            )
            return

        try:
            recipients = json.loads(record.recipients_json)
        except (json.JSONDecodeError, TypeError):  # fmt: skip
            recipients = {}
        try:
            attachments = json.loads(record.attachments_json)
        except (json.JSONDecodeError, TypeError):  # fmt: skip
            attachments = []

        self._serve_json(
            {
                "uid": uid,
                "folder": sent_folder,
                "subject": record.subject,
                "from": record.sender,
                "to": recipients.get("to", []) if isinstance(recipients, dict) else [],
                "cc": recipients.get("cc", []) if isinstance(recipients, dict) else [],
                "date": record.date,
                "body_plain": record.body_plain,
                "body_html": record.body_html,
                "attachments": attachments,
            }
        )
