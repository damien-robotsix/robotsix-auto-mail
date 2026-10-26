"""Archive read-only view service for the board server.

Read-only GET counterpart to the POST-mutation
:class:`~robotsix_auto_mail.server._archive_service.ArchiveService`: these
endpoints browse and read archived messages and folders.  Extracted from
:class:`~robotsix_auto_mail.server._view_service.ViewService` to keep that
module focused on board rendering and email-detail views.

Every endpoint method takes the per-request context
(:class:`robotsix_auto_mail.server._board_handler_protocol.RequestContext`)
and reads all per-request state off it; the service instance itself holds
only the injected dependencies and is safe to share across every request.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast
from urllib.parse import unquote

from robotsix_auto_mail.config import resolve_llm_api_key
from robotsix_auto_mail.server._constants import (
    _parse_archive_structure,
    _with_db,
)
from robotsix_auto_mail.server._services import Service
from robotsix_auto_mail.triage import get_archive_subfolder

if TYPE_CHECKING:
    from robotsix_auto_mail.config import MailConfig
    from robotsix_auto_mail.server._board_handler_protocol import RequestContext

logger = logging.getLogger(__name__)


def _fetch_raw_message(ctx: RequestContext, folder: str, uid: int) -> bytes | None:
    """Fetch raw MIME bytes by folder + UID via read-only ``BODY.PEEK``.

    Returns the raw message bytes, or ``None`` after emitting a 502
    response.
    """
    from robotsix_auto_mail.imap import ImapClient, ImapError

    mail_config = cast("MailConfig", ctx.mail_config)
    try:
        with ImapClient(mail_config) as client:
            client.select_folder(folder)
            fetched = client.fetch_messages([uid])
    except (ImapError, OSError) as exc:
        ctx._problem(
            status=502,
            kind="imap-fetch-failed",
            title="IMAP Fetch Failed",
            detail=f"IMAP fetch failed: {exc}",
        )
        return None

    if not fetched:
        ctx._problem(
            status=502,
            kind="message-unavailable",
            title="Message Unavailable",
            detail="Message no longer exists on the mail server",
        )
        return None

    _uid, raw_bytes = fetched[0]
    return raw_bytes


def _serve_parsed_message_body(
    ctx: RequestContext,
    raw_bytes: bytes,
    *,
    imap_uid: int,
    source_folder: str,
    message_id: str | None,
    prefer_parsed_message_id: bool = False,
    extra: Mapping[str, object] | None = None,
) -> None:
    """Parse *raw_bytes* and serve the JSON message-body response.

    Shared by the board-card body, mailbox message read and archive
    message read endpoints.  ``message_id`` is served verbatim unless
    *prefer_parsed_message_id* is set, in which case the parsed
    ``Message-ID`` header wins and *message_id* is only the fallback.
    *extra* keys (e.g. ``folder``/``uid``) are inserted after
    ``account``.
    """
    import json

    from robotsix_auto_mail.core._sanitize import sanitize_html
    from robotsix_auto_mail.pipeline import parse_message

    parsed = parse_message(
        raw_bytes,
        imap_uid=imap_uid,
        source_folder=source_folder,
    )

    try:
        attachments = json.loads(parsed.attachments_json)
    except (json.JSONDecodeError, TypeError):  # fmt: skip
        attachments = []

    body_html = sanitize_html(parsed.body_html) if parsed.body_html else None

    ctx._serve_json(
        {
            "message_id": (
                (parsed.message_id or message_id)
                if prefer_parsed_message_id
                else message_id
            ),
            "account": ctx._current_account_id or "main",
            **(extra or {}),
            "body_plain": parsed.body_plain,
            "body_html": body_html,
            "attachments": attachments,
        }
    )


class ArchiveViewService(Service):
    """Stateless service providing the board's read-only archive GET endpoints."""

    def serve_archive_proposal(self, ctx: RequestContext) -> None:
        """Serve GET /archive-proposal/{message_id} — return JSON with
        effective subfolder, source, and folder-exists status."""
        from robotsix_auto_mail.db import (
            get_record_by_message_id,
            get_watermark,
        )
        from robotsix_auto_mail.triage import (
            _load_archive_overrides,
            _load_llm_archive_hints,
        )

        path = ctx.path
        prefix = "/archive-proposal/"
        message_id = unquote(path[len(prefix) :])

        archive_root = ctx._effective_archive_root

        with _with_db(ctx.db_path) as conn:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                ctx._not_found()
                return

            subfolder = get_archive_subfolder(
                conn,
                message_id,
                record,
                api_key=resolve_llm_api_key(raise_on_missing=False),
                rules=cast("MailConfig", ctx.mail_config).triage_guidance
                if ctx.mail_config is not None
                else "",
            )
            overrides = _load_archive_overrides(conn)
            hints = _load_llm_archive_hints(conn)

            if message_id in overrides:
                source = "override"
                overridden = True
            elif message_id in hints:
                source = "llm"
                overridden = False
            else:
                source = "rule"
                overridden = False

            # Determine folder_exists
            archive_raw = get_watermark(conn, "archive_structure")
            existing_folders, delimiter, effective_root = _parse_archive_structure(
                archive_raw, archive_root
            )
            if subfolder:
                translated = subfolder.replace("/", delimiter)
                full_path = f"{effective_root}{delimiter}{translated}"
            else:
                full_path = effective_root
            folder_exists = full_path in existing_folders

        ctx._serve_json(
            {
                "subfolder": subfolder,
                "archive_root": archive_root,
                "folder_exists": folder_exists,
                "overridden": overridden,
                "source": source,
            }
        )

    def serve_archive_message(self, ctx: RequestContext) -> None:
        r"""Serve GET /archive/message — read an archived message body.

        Reads a full message body addressed by **archive folder + UID**,
        the read-only counterpart to ``POST /archive-message-delete``
        (``{"source_folder": …, "uid": …}``).  Needed because once a
        message is archived its board card is removed, so its body is no
        longer reachable via ``GET /board-cards/{card_id}/body`` — but it
        is still locatable through ``GET /search`` by folder + uid.

        Query parameters:

        - ``folder`` (required) — the archive folder path.  Accepts both
          the archive-root-relative form (``Anniversaire``) and the full
          IMAP path as returned by ``/search``
          (``robotsix-mail-archive/Anniversaire``); the leading archive
          root is stripped when present.
        - ``uid`` — the IMAP UID within *folder*.
        - ``message_id`` — fallback selector (``HEADER Message-ID``
          search within *folder*) used when *uid* is absent or stale.

        At least one of ``uid`` / ``message_id`` must be given.

        The response mirrors the board-card body endpoint — ``body_plain``,
        ``body_html`` (sanitized or ``null``), and ``attachments``
        metadata only (never the attachment bytes) — plus the resolved
        ``folder`` and ``uid`` for clarity.

        Strictly read-only: the fetch uses IMAP ``BODY.PEEK`` (via
        :meth:`ImapClient.fetch_messages`) so the message's ``\Seen``
        state is left unchanged, and no board card is created or moved.

        Errors: 400 on missing params / aggregate mode, 404 on unknown
        folder or uid, 502 on IMAP error.
        """
        from urllib.parse import parse_qs, urlsplit

        if ctx._aggregate:
            ctx._serve_json(
                {"error": "archive message read is per-account; use ?account=<id>"},
                status=400,
            )
            return

        if not ctx._require_imap_configured():
            return

        qs = parse_qs(urlsplit(ctx.path).query)
        folder = (qs.get("folder") or [""])[0]
        message_id = (qs.get("message_id") or [""])[0]

        uid: int | None = None
        uid_values = qs.get("uid")
        if uid_values:
            try:
                uid = int(uid_values[0])
            except (ValueError, TypeError):  # fmt: skip
                ctx._bad_request("uid must be an integer")
                return

        if not folder:
            ctx._bad_request("folder is required")
            return

        if uid is None and not message_id:
            ctx._bad_request("At least one of uid or message_id is required")
            return

        ok, archive_root = ctx._validate_archive_path(folder)
        if not ok:
            return

        # Accept both the archive-root-relative form ("Anniversaire") and the
        # full IMAP path as returned by ``/search``
        # ("robotsix-mail-archive/Anniversaire"): strip the leading archive
        # root when present so both resolve to the same folder.
        root_prefix = f"{archive_root}/"
        if folder == archive_root:
            rel_folder = ""
        elif folder.startswith(root_prefix):
            rel_folder = folder[len(root_prefix) :]
        else:
            rel_folder = folder

        full_path = f"{archive_root}/{rel_folder}" if rel_folder else archive_root

        from robotsix_auto_mail.imap import ImapClient, ImapError

        resolved_uid: int | None = None
        raw_bytes: bytes | None = None

        try:
            with ImapClient(cast("MailConfig", ctx.mail_config)) as client:
                existing = client.list_folders()
                delimiter = next(
                    (f.delimiter for f in existing if f.delimiter),
                    "/",
                )

                translated_path = full_path.replace("/", delimiter)

                # Security: validate the folder is under the archive root.
                ar_translated = archive_root.replace("/", delimiter)
                prefix = f"{ar_translated}{delimiter}"
                if translated_path != ar_translated and not translated_path.startswith(
                    prefix
                ):
                    ctx._bad_request("folder escapes archive root")
                    return

                matching = [f for f in existing if f.name == translated_path]
                if not matching:
                    ctx._not_found()
                    return

                client.select_folder(translated_path)

                # Resolve the UID, falling back to a Message-ID search when
                # the provided uid is absent or stale.
                if uid is not None and client.search_uids(f"UID {uid}"):
                    resolved_uid = uid
                elif message_id:
                    found = client.search_uids(f'HEADER Message-ID "{message_id}"')
                    if found:
                        resolved_uid = found[0]

                if resolved_uid is None:
                    ctx._not_found()
                    return

                fetched = client.fetch_messages([resolved_uid])
        except (ImapError, OSError) as exc:
            ctx._problem(
                status=502,
                kind="imap-fetch-failed",
                title="IMAP Fetch Failed",
                detail=f"IMAP fetch failed: {exc}",
            )
            return

        if not fetched:
            ctx._not_found()
            return

        _uid, raw_bytes = fetched[0]

        _serve_parsed_message_body(
            ctx,
            raw_bytes,
            imap_uid=resolved_uid,
            source_folder=translated_path,
            message_id=message_id,
            prefer_parsed_message_id=True,
            extra={"folder": folder, "uid": resolved_uid},
        )

    def serve_archive_folders(self, ctx: RequestContext) -> None:
        """Serve GET /archive-folders — JSON with delimiter + flat subfolder list.

        Lists the real IMAP folder tree under the effective archive root.
        Falls back to the ``archive_structure`` watermark when IMAP is
        unreachable or *mail_config* is not available.

        Short-circuits in aggregate (``?account=__all__``) mode — the JS
        already suppresses the fetch, but a direct ``curl`` must not leak
        data from whichever account's DB ``ctx.db_path`` happens to point at.
        """
        from robotsix_auto_mail.db import get_watermark

        if ctx._aggregate:
            ctx._serve_json({"delimiter": "/", "folders": []})
            return

        archive_root = ctx._effective_archive_root

        # -- try IMAP first -------------------------------------------------
        if ctx.mail_config is not None:
            try:
                from robotsix_auto_mail.imap import ImapClient

                with ImapClient(cast("MailConfig", ctx.mail_config)) as client:
                    all_folders = client.list_folders()
                    delimiter = next(
                        (f.delimiter for f in all_folders if f.delimiter), "/"
                    )
                    _root_prefix = f"{archive_root}{delimiter}"
                    folders: list[str] = []
                    for f in sorted(all_folders, key=lambda f: f.name):
                        if f.name.startswith(_root_prefix) and f.name != archive_root:
                            rel = f.name[len(_root_prefix) :]
                            if delimiter != "/":
                                rel = rel.replace(delimiter, "/")
                            folders.append(rel)
                    ctx._serve_json({"delimiter": "/", "folders": folders})
                    return
            except Exception:
                logger.debug("IMAP folder list fallback to watermark", exc_info=True)
                # Fall through to watermark fallback.

        # -- watermark fallback ---------------------------------------------
        with _with_db(ctx.db_path) as conn:
            archive_raw = get_watermark(conn, "archive_structure")
            existing_folders, delimiter, effective_root = _parse_archive_structure(
                archive_raw, archive_root
            )

        _root_prefix = f"{effective_root}{delimiter}"
        folders = []
        for name in sorted(existing_folders):
            if name.startswith(_root_prefix) and name != effective_root:
                rel = name[len(_root_prefix) :]
                if delimiter != "/":
                    rel = rel.replace(delimiter, "/")
                folders.append(rel)

        ctx._serve_json({"delimiter": "/", "folders": folders})

    def serve_archive_messages(self, ctx: RequestContext, folder: str = "") -> None:
        """Serve GET /archive/<folder>/messages — list messages in an archive folder.

        Connects to IMAP, resolves the folder path under the effective
        archive root, selects it, and returns envelope metadata for every
        message present.  Returns an empty list when the folder is empty.

        Supports an optional ``?limit=N`` query parameter (default 500,
        max 2000) to cap the number of messages returned.

        Short-circuits in aggregate (``?account=__all__``) mode.
        """
        if ctx._aggregate:
            ctx._serve_json({"messages": [], "folder": folder or ""})
            return

        if not ctx._require_imap_configured():
            return

        ok, archive_root = ctx._validate_archive_path(folder)
        if not ok:
            return

        # Resolve the full IMAP folder path.
        # Translate "/" in the URL path to the IMAP delimiter.
        # We'll discover the actual delimiter from the server, but
        # the URL always uses "/".
        full_path = f"{archive_root}/{folder}" if folder else archive_root

        from robotsix_auto_mail.imap import ImapClient, ImapError

        try:
            with ImapClient(cast("MailConfig", ctx.mail_config)) as client:
                # Discover the server's hierarchy delimiter.
                existing = client.list_folders()
                delimiter = next(
                    (f.delimiter for f in existing if f.delimiter),
                    "/",
                )

                # Translate "/" in full_path to the server delimiter.
                translated_path = full_path.replace("/", delimiter)

                # Security: validate the path is under the archive root.
                root_prefix = f"{archive_root.replace('/', delimiter)}{delimiter}"
                if translated_path != archive_root.replace(
                    "/", delimiter
                ) and not translated_path.startswith(root_prefix):
                    ctx._bad_request("Folder path escapes archive root")
                    return

                # Verify the folder exists.
                matching = [f for f in existing if f.name == translated_path]
                if not matching:
                    ctx._not_found()
                    return

                client.select_folder(translated_path)
                all_uids = client.search_uids("ALL")

                # Parse limit query parameter.
                from urllib.parse import parse_qs, urlsplit

                qs = parse_qs(urlsplit(ctx.path).query)
                limit_str = qs.get("limit", ["500"])[0]
                try:
                    limit = min(max(int(limit_str), 1), 2000)
                except ValueError, TypeError:
                    limit = 500

                uids = all_uids[:limit]

                envelopes = client.fetch_envelopes(uids)
        except ImapError as exc:
            ctx._send_response(
                f"IMAP error listing archive folder: {exc}",
                status=502,
            )
            return
        except OSError as exc:
            ctx._send_response(
                f"IMAP connection error: {exc}",
                status=502,
            )
            return

        # Build response — include the effective path for clarity.
        ctx._serve_json(
            {
                "folder": folder if folder else archive_root,
                "full_path": full_path,
                "total": len(all_uids),
                "shown": len(envelopes),
                "messages": envelopes,
            }
        )
