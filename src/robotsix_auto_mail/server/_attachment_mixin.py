"""Attachment-to-file-hub mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import email
import email.header
import io
import json
import logging
import mimetypes
import zipfile
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import httpx

from robotsix_auto_mail.server._action_mixin import _json_field_value
from robotsix_auto_mail.server._constants import _with_db

logger = logging.getLogger(__name__)

#: Zip-bomb guards for the unzip-during-push path.  The total uncompressed
#: size and the file count of a single zip attachment are capped; a zip
#: that exceeds either cap is rejected with 400 rather than expanded and
#: pushed.
_MAX_UNZIP_TOTAL_BYTES = 500 * 1024 * 1024
_MAX_UNZIP_FILE_COUNT = 1000


def _decode_mime_header(value: str | None) -> str:
    """Decode an RFC 2047 encoded-word header into a plain string.

    Returns the empty string for a missing header and falls back to the
    raw value when decoding fails.
    """
    if not value:
        return ""
    try:
        return str(email.header.make_header(email.header.decode_header(value)))
    except ValueError, LookupError:
        return value


def _iter_attachment_parts(
    msg: email.message.Message,
) -> Iterator[tuple[int, str | None, str, bytes]]:
    """Yield ``(index, filename, content_type, payload)`` per attachment part.

    Walks the MIME tree in document order using the same disposition
    heuristic as the board detail view: a part counts as an attachment
    when it is explicitly ``Content-Disposition: attachment`` or is a
    non-inline part whose type is not ``text/plain`` / ``text/html``.
    """
    idx = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        content_type = part.get_content_type()
        if disposition == "attachment" or (
            disposition != "inline" and content_type not in ("text/plain", "text/html")
        ):
            raw_payload = part.get_payload(decode=True)
            payload = raw_payload if isinstance(raw_payload, bytes) else b""
            yield idx, part.get_filename(), content_type, payload
            idx += 1


class _AttachmentMixin:
    """Mixin providing POST /email/<id>/attachments/to-file-hub."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    def _handle_push_to_file_hub(self, message_id: str) -> None:
        """Push one or all attachments of a message to robotsix-file-hub.

        POST /email/<message_id>/attachments/to-file-hub

        The message is addressed one of two ways:

        - **Board message** (default): the ``<message_id>`` path segment
          is resolved against the triaged board state.
        - **Archive message**: when the JSON body carries
          ``source_folder``/``folder`` (relative to the archive root)
          plus ``uid`` (with the path ``message_id`` as an optional
          fallback selector), the message is resolved directly from the
          archive IMAP folder — mirroring ``POST /archive-message-delete``.
          The account is selected via the ``?account=<id>`` query param.

        Optional JSON body keys (all modes):
          {"filename": "invoice.pdf"}  — push a single attachment by name
          {"index": 0}                 — push a single attachment by index
          {} or omitted               — push all attachments
          {"unzip": true}             — expand zip attachments (default)
          {"context": "...", "tags": [...]}  — provenance forwarded to
                                         file-hub for triage/classification

        Returns JSON with one entry per file that landed in file-hub.
        """
        # -- check file-hub is configured ----------------------------------
        accounts = self.accounts
        file_hub_url: str = ""
        if accounts is not None:
            file_hub_url = getattr(accounts, "file_hub_url", "") or ""
        if not file_hub_url:
            self._problem(
                status=503,
                kind="file-hub-not-configured",
                title="File-hub Not Configured",
                detail="file-hub is not configured (set file_hub_url in config)",
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

        # -- resolve the message (board vs archive addressing) -------------
        archive_mode = (
            bool(_json_field_value(selector, "source_folder"))
            or bool(_json_field_value(selector, "folder"))
            or selector.get("uid") is not None
        )
        if archive_mode:
            prepared = self._prepare_archive_push(selector, message_id)
        else:
            prepared = self._prepare_board_push(selector, message_id)
        if prepared is None:
            return  # a response has already been sent
        msg, attachments_meta, selected_indices, provenance = prepared

        # -- extract, optionally unzip, and upload -------------------------
        self._extract_and_upload(
            msg,
            attachments_meta,
            selected_indices,
            selector,
            provenance,
            file_hub_url,
        )

    # -- board-message resolution -----------------------------------------

    def _prepare_board_push(
        self, selector: dict[str, Any], message_id: str
    ) -> tuple[email.message.Message, list[Any], set[int], dict[str, str]] | None:
        """Resolve a board message by Message-ID.

        Returns ``(msg, attachments_meta, selected_indices, provenance)``
        or ``None`` when a response has already been sent.
        """
        from robotsix_auto_mail.db import get_record_by_message_id

        with _with_db(self.db_path, skip_migrations=True) as conn:
            record = get_record_by_message_id(conn, message_id)
        if record is None:
            self._not_found()
            return None

        try:
            attachments_meta = json.loads(record.attachments_json)
        except json.JSONDecodeError, TypeError:
            attachments_meta = []
        if not isinstance(attachments_meta, list) or not attachments_meta:
            self._problem(
                status=400,
                kind="no-attachments",
                title="No Attachments",
                detail="Message has no attachments",
            )
            return None

        selected_indices = self._resolve_selected_indices(selector, attachments_meta)
        if selected_indices is None:
            return None

        if self.mail_config is None or record.imap_uid is None:
            self._problem(
                status=502,
                kind="imap-unavailable",
                title="IMAP Unavailable",
                detail="IMAP not available for this message",
            )
            return None

        msg = self._fetch_mime(record.source_folder, record.imap_uid)
        if msg is None:
            return None

        provenance = {
            "account": self._current_account_id or "main",
            "source_folder": record.source_folder,
            "message_id": record.message_id,
            "subject": record.subject,
            "sender": record.sender,
            "date": record.date,
        }
        return msg, attachments_meta, selected_indices, provenance

    def _fetch_mime(self, folder: str, uid: int) -> email.message.Message | None:
        """Fetch and parse a raw message by folder + UID.

        Returns the parsed :class:`email.message.Message` or ``None``
        after emitting a 502 response.
        """
        from robotsix_auto_mail.imap import ImapClient, ImapError

        try:
            with ImapClient(self.mail_config) as client:
                client.select_folder(folder)
                fetched = client.fetch_messages([uid])
        except (ImapError, OSError) as exc:
            self._problem(
                status=502,
                kind="imap-fetch-failed",
                title="IMAP Fetch Failed",
                detail=f"IMAP fetch failed: {exc}",
            )
            return None

        if not fetched:
            self._problem(
                status=502,
                kind="message-unavailable",
                title="Message Unavailable",
                detail="Message no longer exists on the mail server",
            )
            return None

        _uid, raw_bytes = fetched[0]
        return email.message_from_bytes(raw_bytes)

    # -- archive-message resolution ---------------------------------------

    def _prepare_archive_push(
        self, selector: dict[str, Any], path_message_id: str
    ) -> tuple[email.message.Message, list[Any], set[int], dict[str, str]] | None:
        """Resolve an archive-resident message by folder + uid.

        Mirrors ``POST /archive-message-delete``: ``source_folder``
        (relative to the archive root) plus ``uid``, with ``message_id``
        as a fallback selector within that folder.  Returns
        ``(msg, attachments_meta, selected_indices, provenance)`` or
        ``None`` when a response has already been sent.
        """
        source_folder = _json_field_value(selector, "source_folder") or (
            _json_field_value(selector, "folder")
        )
        if not source_folder:
            self._bad_request(
                "source_folder (or folder) is required for archive addressing"
            )
            return None

        uid_raw = selector.get("uid")
        uid: int | None = None
        if uid_raw is not None:
            try:
                uid = int(uid_raw)
            except ValueError, TypeError:
                self._bad_request("uid must be an integer")
                return None

        message_id = _json_field_value(selector, "message_id") or path_message_id
        if uid is None and not message_id:
            self._bad_request("At least one of uid or message_id is required")
            return None

        if self.mail_config is None:
            self._problem(
                status=502,
                kind="imap-unavailable",
                title="IMAP Unavailable",
                detail="IMAP not available for this account",
            )
            return None

        ok, archive_root = self._validate_archive_path(source_folder)
        if not ok:
            return None

        msg = self._fetch_archive_mime(archive_root, source_folder, uid, message_id)
        if msg is None:
            return None

        attachments_meta: list[Any] = [
            {"filename": fname or "attachment", "mime_type": ctype}
            for _idx, fname, ctype, _payload in _iter_attachment_parts(msg)
        ]
        if not attachments_meta:
            self._problem(
                status=400,
                kind="no-attachments",
                title="No Attachments",
                detail="Message has no attachments",
            )
            return None

        selected_indices = self._resolve_selected_indices(selector, attachments_meta)
        if selected_indices is None:
            return None

        provenance = {
            "account": self._current_account_id or "main",
            "source_folder": source_folder,
            "message_id": _decode_mime_header(msg.get("Message-ID")) or message_id,
            "subject": _decode_mime_header(msg.get("Subject")),
            "sender": _decode_mime_header(msg.get("From")),
            "date": _decode_mime_header(msg.get("Date")),
        }
        return msg, attachments_meta, selected_indices, provenance

    def _fetch_archive_mime(
        self,
        archive_root: str,
        source_folder: str,
        uid: int | None,
        message_id: str,
    ) -> email.message.Message | None:
        """Resolve + fetch an archive message, mirroring the delete path.

        Returns the parsed message or ``None`` after emitting a
        400/404/502 response.
        """
        from robotsix_auto_mail.imap import (
            ImapClient,
            ImapError,
            ImapMessageNotFoundError,
        )

        resolved_uid: int | None = None
        try:
            with ImapClient(self.mail_config) as client:
                existing = client.list_folders()
                delimiter = next(
                    (f.delimiter for f in existing if f.delimiter),
                    "/",
                )

                translated_source = f"{archive_root}/{source_folder}".replace(
                    "/", delimiter
                )
                root_prefix = f"{archive_root.replace('/', delimiter)}{delimiter}"
                ar_translated = archive_root.replace("/", delimiter)
                if (
                    translated_source != ar_translated
                    and not translated_source.startswith(root_prefix)
                ):
                    self._bad_request("source_folder escapes archive root")
                    return None

                client.select_folder(translated_source)

                if uid is not None:
                    if client.search_uids(f"UID {uid}"):
                        resolved_uid = uid
                    elif message_id:
                        found = client.search_uids(f'HEADER Message-ID "{message_id}"')
                        if found:
                            resolved_uid = found[0]
                else:
                    found = client.search_uids(f'HEADER Message-ID "{message_id}"')
                    if found:
                        resolved_uid = found[0]

                if resolved_uid is None:
                    self._not_found()
                    return None

                fetched = client.fetch_messages([resolved_uid])
        except ImapMessageNotFoundError:
            self._not_found()
            return None
        except (ImapError, OSError) as exc:
            self._problem(
                status=502,
                kind="imap-fetch-failed",
                title="IMAP Fetch Failed",
                detail=f"IMAP fetch failed: {exc}",
            )
            return None

        if not fetched:
            self._problem(
                status=502,
                kind="message-unavailable",
                title="Message Unavailable",
                detail="Message no longer exists on the mail server",
            )
            return None

        _uid, raw_bytes = fetched[0]
        return email.message_from_bytes(raw_bytes)

    # -- attachment selection ---------------------------------------------

    def _resolve_selected_indices(
        self, selector: dict[str, Any], attachments_meta: list[Any]
    ) -> set[int] | None:
        """Resolve the ``filename``/``index`` selector to attachment indices.

        Returns the selected index set (all indices when no selector is
        given) or ``None`` after emitting a 400/404 response.
        """
        filename_filter = selector.get("filename")
        index_filter = selector.get("index")

        if filename_filter is not None:
            if not isinstance(filename_filter, str):
                self._bad_request("filename must be a string")
                return None
            matching = [
                i
                for i, a in enumerate(attachments_meta)
                if isinstance(a, dict) and a.get("filename") == filename_filter
            ]
            if not matching:
                self._problem(
                    status=404,
                    kind="attachment-not-found",
                    title="Attachment Not Found",
                    detail=f"Attachment not found: {filename_filter}",
                )
                return None
            return {matching[0]}

        if index_filter is not None:
            if not isinstance(index_filter, int) or index_filter < 0:
                self._bad_request("index must be a non-negative integer")
                return None
            if index_filter >= len(attachments_meta):
                self._problem(
                    status=400,
                    kind="index-out-of-range",
                    title="Attachment Index Out of Range",
                    detail=(
                        f"Index {index_filter} out of range "
                        f"(message has {len(attachments_meta)} attachments)"
                    ),
                )
                return None
            return {index_filter}

        return set(range(len(attachments_meta)))

    # -- zip handling -----------------------------------------------------

    def _extract_zip(self, payload: bytes) -> list[tuple[str, bytes]] | None:
        """Extract every contained file from a zip *payload*.

        Returns ``[(inner_filename, bytes), ...]`` preserving inner base
        names, or ``None`` when the archive exceeds the zip-bomb caps
        (total uncompressed size or file count).
        """
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            infos = [zi for zi in zf.infolist() if not zi.is_dir()]
            if len(infos) > _MAX_UNZIP_FILE_COUNT:
                return None
            if sum(zi.file_size for zi in infos) > _MAX_UNZIP_TOTAL_BYTES:
                return None
            out: list[tuple[str, bytes]] = []
            for zi in infos:
                inner = zi.filename.rsplit("/", 1)[-1]
                if not inner:
                    continue
                out.append((inner, zf.read(zi)))
        return out

    # -- upload -----------------------------------------------------------

    def _extract_and_upload(
        self,
        msg: email.message.Message,
        attachments_meta: list[Any],
        selected_indices: set[int],
        selector: dict[str, Any],
        provenance: dict[str, str],
        file_hub_url: str,
    ) -> None:
        """Extract the selected attachment parts, optionally expand zips,
        and upload each resulting file to file-hub with provenance
        metadata.
        """
        unzip = selector.get("unzip", True)
        if not isinstance(unzip, bool):
            self._bad_request("unzip must be a boolean")
            return
        context = selector.get("context")
        if context is None:
            context = selector.get("note")
        if context is not None and not isinstance(context, str):
            self._bad_request("context must be a string")
            return
        tags = selector.get("tags")
        if tags is not None and not isinstance(tags, list):
            self._bad_request("tags must be a list")
            return

        # -- gather the selected attachment parts --------------------------
        selected_parts: list[tuple[str, str, bytes]] = []
        for idx, part_fname, part_ctype, payload in _iter_attachment_parts(msg):
            if idx not in selected_indices or idx >= len(attachments_meta):
                continue
            meta = attachments_meta[idx]
            filename = (
                (meta.get("filename") if isinstance(meta, dict) else None)
                or part_fname
                or "attachment"
            )
            mime_type = (
                (meta.get("mime_type") if isinstance(meta, dict) else None)
                or part_ctype
                or "application/octet-stream"
            )
            selected_parts.append((filename, mime_type, payload))

        if not selected_parts:
            self._problem(
                status=502,
                kind="no-matching-attachments",
                title="No Matching Attachments",
                detail="No matching attachment parts found in MIME",
            )
            return

        # -- expand zips into concrete upload jobs -------------------------
        # Each job: (filename, mime_type, payload, containing_zip_name|None)
        upload_jobs: list[tuple[str, str, bytes, str | None]] = []
        for filename, mime_type, payload in selected_parts:
            if unzip and zipfile.is_zipfile(io.BytesIO(payload)):
                extracted = self._extract_zip(payload)
                if extracted is None:
                    self._problem(
                        status=400,
                        kind="zip-too-large",
                        title="Zip Archive Too Large",
                        detail=(
                            "Zip archive exceeds extraction caps "
                            f"(max {_MAX_UNZIP_FILE_COUNT} files, "
                            f"{_MAX_UNZIP_TOTAL_BYTES} bytes uncompressed)"
                        ),
                    )
                    return
                for inner_name, inner_bytes in extracted:
                    inner_mime = (
                        mimetypes.guess_type(inner_name)[0]
                        or "application/octet-stream"
                    )
                    upload_jobs.append((inner_name, inner_mime, inner_bytes, filename))
            else:
                upload_jobs.append((filename, mime_type, payload, None))

        # -- upload each job to file-hub -----------------------------------
        upload_url = f"{file_hub_url.rstrip('/')}/files"
        results: list[dict[str, Any]] = []

        for up_filename, up_mime, up_payload, zip_name in upload_jobs:
            outer_filename = zip_name if zip_name is not None else up_filename
            metadata = self._build_metadata(
                provenance, outer_filename, zip_name, context, tags
            )
            try:
                with httpx.Client(timeout=60) as client:
                    resp = client.post(
                        upload_url,
                        files={"file": (up_filename, up_payload, up_mime)},
                        data={"metadata": json.dumps(metadata)},
                    )
            except Exception as exc:
                self._problem(
                    status=502,
                    kind="file-hub-upload-failed",
                    title="File-hub Upload Failed",
                    detail=f"file-hub upload failed: {exc}",
                )
                return

            if resp.status_code >= 400:
                self._problem(
                    status=502,
                    kind="file-hub-error",
                    title="File-hub Error",
                    detail=f"file-hub returned {resp.status_code}: {resp.text}",
                )
                return

            results.append(resp.json())

        self._send_response(
            json.dumps({"attachments": results}),
            status=200,
            content_type="application/json; charset=utf-8",
        )

    @staticmethod
    def _build_metadata(
        provenance: dict[str, str],
        outer_filename: str,
        zip_name: str | None,
        context: str | None,
        tags: list[Any] | None,
    ) -> dict[str, Any]:
        """Build the provenance metadata forwarded to file-hub per file."""
        metadata: dict[str, Any] = {
            "source_account": provenance.get("account", ""),
            "source_folder": provenance.get("source_folder", ""),
            "source_message_id": provenance.get("message_id", ""),
            "mail_subject": provenance.get("subject", ""),
            "mail_sender": provenance.get("sender", ""),
            "mail_date": provenance.get("date", ""),
            "attachment_filename": outer_filename,
        }
        if zip_name is not None:
            metadata["zip_name"] = zip_name
        if context is not None:
            metadata["context"] = context
        if tags is not None:
            metadata["tags"] = tags
        return metadata
