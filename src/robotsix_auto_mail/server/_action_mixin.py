"""Action-handler mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

from robotsix_auto_mail.config import DEFAULT_ARCHIVE_ROOT, MailConfig
from robotsix_auto_mail.db import MailRecord, get_watermark, init_db, set_watermark
from robotsix_auto_mail.server._constants import _is_safe_redirect_path
from robotsix_auto_mail.triage import (
    TO_ANSWER,
    TO_ARCHIVE,
    TO_CALENDAR,
    VALID_TRIAGE_ACTIONS,
    get_archive_subfolder,
    get_triage_decision,
    propose_archive_subfolder_llm,
    record_archive_folder_choice,
    record_human_decision,
    set_triage_decision,
)


class _BoardActionMixin:
    """Mixin providing POST action handlers for the board server."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    def _launch_background_worker(
        self,
        watermark_key: str,
        target: Callable[..., None] | None = None,
        args: tuple[Any, ...] = (),
        *,
        running_check: Callable[[str | None], bool] | None = None,
        precheck: Callable[[Any], bool] | None = None,
        db_path: str | None = None,
        redirect: bool = True,
    ) -> bool:
        """Acquire a single-flight watermark and optionally spawn a daemon thread.

        Returns ``True`` when the watermark was acquired (and, when *target*
        is not ``None``, the worker thread was started).  Returns ``False``
        when the watermark is already held or *precheck* returns ``False``.

        When *redirect* is ``True`` (the default) the handler redirects to
        ``/board`` on both the failure paths **and** after a successful
        spawn.  Set *redirect* to ``False`` when the caller needs to
        control the response itself (e.g. in an aggregate fan-out loop).
        """
        _path = db_path if db_path is not None else self.db_path

        conn = init_db(_path, skip_migrations=True)
        try:
            if precheck is not None and not precheck(conn):
                if redirect:
                    self._redirect("/board", code=302)
                return False

            if running_check is not None:
                _is_running = running_check
            else:

                def _is_running(s: str | None) -> bool:
                    return s == "running"

            if _is_running(get_watermark(conn, watermark_key)):
                if redirect:
                    self._redirect("/board", code=302)
                return False

            set_watermark(conn, watermark_key, "running")
        finally:
            conn.close()

        if target is not None:
            threading.Thread(target=target, args=args, daemon=True).start()
            if redirect:
                self._redirect("/board", code=302)

        return True

    def _parse_request_body(
        self, *fields: str, no_strip: frozenset[str] = frozenset()
    ) -> dict[str, str]:
        """Parse the request body as URL-encoded form data.

        Returns a dict mapping each requested *field* name to its
        first value.  Values are stripped of leading/trailing
        whitespace *unless* the field name appears in *no_strip*.
        """
        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        parsed = parse_qs(raw)
        return {
            field: (
                (parsed.get(field) or [""])[0].strip()
                if field not in no_strip
                else (parsed.get(field) or [""])[0]
            )
            for field in fields
        }

    def _handle_post_action(
        self,
        *fields: str,
        action: Any,
        no_strip: frozenset[str] = frozenset(),
    ) -> None:
        """Shared POST handler skeleton.

        1. Parses the request body for the declared *fields*.
        2. Validates ``message_id`` (returns 400 if missing).
        3. Opens a read-only DB connection, looks up the record,
           and returns 404 if absent.
        4. Delegates to *action(conn, record, redirect_to,
           \\*\\*extra_fields)* for the handler-specific logic.
           When *action* returns ``False`` the redirect is skipped
           (the callback already sent a response).
        5. Closes the connection and performs a safe redirect.
        """
        from robotsix_auto_mail.db import get_record_by_message_id, init_db

        f = self._parse_request_body(*fields, no_strip=no_strip)
        message_id = f.get("message_id", "")
        redirect_to = f.get("redirect_to", "")

        if not message_id:
            self._bad_request("Missing message_id")
            return

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._not_found()
                return

            extra = {
                k: v for k, v in f.items() if k not in ("message_id", "redirect_to")
            }
            if action(conn, record, redirect_to, **extra) is False:
                return
        finally:
            conn.close()

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
            self._redirect("/board", code=302)

    def _handle_move(self) -> None:
        """Process POST /move — update a card's triage decision and redirect."""

        def move_action(
            conn: Any, record: MailRecord, redirect_to: str, triage_action: str
        ) -> bool:
            if not triage_action:
                self._bad_request("Missing triage_action")
                return False
            if triage_action not in VALID_TRIAGE_ACTIONS:
                self._bad_request(f"Invalid triage action: {triage_action!r}")
                return False

            message_id = record.message_id

            if (
                triage_action == TO_CALENDAR
                and self.mail_config is not None
                and not self.mail_config.calendar_enabled
            ):
                self._bad_request("Calendar integration is disabled")
                return False

            # Capture prior triage decision for TO_CALENDAR reroute logic
            # before we overwrite it with the move target.
            prior_action: str | None = None
            if triage_action == TO_CALENDAR:
                prior = get_triage_decision(conn, message_id)
                prior_action = prior.action if prior is not None else None

            try:
                set_triage_decision(
                    conn,
                    message_id,
                    triage_action,
                    source="user",
                    reason=f"moved to {triage_action}",
                )
                record_human_decision(conn, message_id, triage_action)
            except sqlite3.IntegrityError:
                # Defense in depth: a stale CHECK constraint (legacy DB
                # predating a new triage action) makes the upsert raise
                # IntegrityError.  Persisting the decision is impossible,
                # but the move must not crash the worker into a 502 — send
                # a clean error response and skip the success redirect.
                self._bad_request(f"Could not move to {triage_action}")
                return False

            if triage_action == TO_CALENDAR:
                # -- Dispatch calendar request on column move ----------
                try:
                    from robotsix_auto_mail.calendar import (
                        CalendarDispatchError,
                        CalendarEventRequest,
                        dispatch_calendar_request,
                        extract_dates_from_body,
                    )
                    from robotsix_auto_mail.db import (
                        update_calendar_correlation_id,
                        update_calendar_event_ref,
                    )
                    from robotsix_auto_mail.format import _effective_body_plain

                    body_text = _effective_body_plain(record)
                    event = CalendarEventRequest(
                        message_id=record.message_id,
                        subject=record.subject,
                        sender=record.sender,
                        body_text=body_text,
                        email_date=record.date,
                        extracted_dates=extract_dates_from_body(body_text),
                    )
                    update_calendar_correlation_id(
                        conn, message_id, event.correlation_id
                    )

                    # Dispatch in a background daemon thread so a slow/hung
                    # transport never blocks the HTTP request. The request's
                    # correlated reply updates the card (event ref or error)
                    # and reroutes it once the calendar agent responds.
                    import threading

                    def _dispatch_bg() -> None:
                        from robotsix_auto_mail.db import init_db

                        try:
                            bg_conn = init_db(self.db_path, skip_migrations=True)
                        except Exception:
                            # Cannot open the DB at all — nothing we
                            # can do.  The daemon thread will exit
                            # silently; the main request already
                            # completed with the card in TO_CALENDAR.
                            return
                        try:
                            try:
                                response = dispatch_calendar_request(
                                    event, config=self.mail_config
                                )
                            except CalendarDispatchError as exc:
                                update_calendar_event_ref(
                                    bg_conn, message_id, f"error: {exc}"
                                )
                            except Exception:
                                update_calendar_event_ref(
                                    bg_conn, message_id, "error: Internal error"
                                )
                            else:
                                # Record the calendar agent's confirmation so
                                # the card shows a success indicator.
                                update_calendar_event_ref(
                                    bg_conn,
                                    message_id,
                                    response.event_ref or "Event created",
                                )
                                # Reroute: if the prior triage decision
                                # was TO_ANSWER the mail still needs a
                                # reply; otherwise it goes to the
                                # archive column.
                                next_action = (
                                    TO_ANSWER
                                    if prior_action == TO_ANSWER
                                    else TO_ARCHIVE
                                )
                                set_triage_decision(
                                    bg_conn,
                                    message_id,
                                    next_action,
                                    source="agent",
                                    reason=(
                                        "calendar dispatched — rerouted"
                                        " from TO_CALENDAR"
                                    ),
                                )
                        finally:
                            bg_conn.close()

                    threading.Thread(target=_dispatch_bg, daemon=True).start()
                except Exception:
                    # Any failure in setup (imports, body extraction,
                    # correlation-id update, thread start) records an
                    # error indicator and the card lands in TO_CALENDAR
                    # — the move always completes normally.
                    try:
                        from robotsix_auto_mail.db import update_calendar_event_ref

                        update_calendar_event_ref(
                            conn, message_id, "error: Internal error"
                        )
                    except Exception:  # noqa: S110  # nosec B110
                        pass

            if triage_action == TO_ARCHIVE:
                try:
                    if self.mail_config is not None:
                        propose_archive_subfolder_llm(
                            conn,
                            record,
                            self.mail_config.llm_api_key,
                            provider_model=(
                                self.mail_config.llm_provider_model
                                if self.mail_config
                                else None
                            ),
                        )
                except Exception:  # noqa: S110  # nosec B110
                    pass  # Non-fatal: board falls back to deterministic proposal
            return True

        self._handle_post_action(
            "message_id",
            "triage_action",
            "redirect_to",
            action=move_action,
        )

    def _handle_delete(self) -> None:
        """Process POST /delete — delete mail from IMAP mailbox and local DB."""
        from robotsix_auto_mail.db import delete_record_by_message_id

        def delete_action(conn: Any, record: MailRecord, redirect_to: str) -> bool:
            # -- IMAP deletion (when config and UID are both available) --
            if self.mail_config is not None and record.imap_uid is not None:
                from robotsix_auto_mail.imap import (
                    ImapClient,
                    ImapError,
                    ImapMessageNotFoundError,
                    resolve_uid_with_fallback,
                )

                try:
                    with ImapClient(self.mail_config) as client:
                        resolved_uid = resolve_uid_with_fallback(
                            client,
                            record.source_folder,
                            record.imap_uid,
                            record.message_id,
                        )
                        client.delete_message(resolved_uid)
                except ImapMessageNotFoundError:
                    from robotsix_auto_mail.db import update_record_source
                    from robotsix_auto_mail.imap import cross_folder_resolve

                    try:
                        with ImapClient(self.mail_config) as client2:
                            cross = cross_folder_resolve(client2, record.message_id)
                            if cross is not None:
                                new_folder, new_uid = cross
                                update_record_source(
                                    conn,
                                    record.message_id,
                                    source_folder=new_folder,
                                    imap_uid=new_uid,
                                )
                                client2.delete_message(new_uid)
                    except (ImapError, OSError) as exc:
                        self._send_response(
                            f"IMAP cross-folder resolution failed: {exc}",
                            status=502,
                        )
                        return False
                except (ImapError, OSError) as exc:
                    self._send_response(
                        f"IMAP deletion failed: {exc}",
                        status=502,
                    )
                    return False

            # -- local DB deletion --
            delete_record_by_message_id(conn, record.message_id)
            return True

        self._handle_post_action(
            "message_id",
            "redirect_to",
            action=delete_action,
        )

    def _imap_archive_move(
        self,
        mail_config: MailConfig,
        imap_uid: int,
        effective_root: str,
        subfolder: str | None,
        source_folder: str = "INBOX",
        message_id: str = "",
    ) -> None:
        """Move a message to the archive folder via IMAP.

        Selects *source_folder* (the record's origin folder) rather
        than assuming ``config.imap_folder``.  If the stored UID is
        stale, falls back to a ``HEADER Message-ID`` search before
        giving up.

        Raises ValueError on security-policy violations (caller should
        return 400).  Raises ImapError or OSError on IMAP/IO failures
        (caller should return 502).
        """
        from robotsix_auto_mail.imap import ImapClient, resolve_uid_with_fallback
        from robotsix_auto_mail.server.adapters import _archive_dest_folder

        with ImapClient(mail_config) as client:
            # Resolve the possibly-stale UID, selecting source_folder.
            resolved_uid = resolve_uid_with_fallback(
                client, source_folder, imap_uid, message_id
            )

            # Determine the IMAP hierarchy delimiter.
            existing = client.list_folders()
            delimiter = next(
                (f.delimiter for f in existing if f.delimiter),
                "/",
            )

            dest_folder = _archive_dest_folder(effective_root, subfolder, delimiter)
            if dest_folder is None:
                raise ValueError("Archive destination escapes archive root")

            # -- ensure destination folder hierarchy exists ----
            parts = dest_folder.split(delimiter)
            for i in range(1, len(parts) + 1):
                client.create_folder(delimiter.join(parts[:i]))

            client.move_message(resolved_uid, dest_folder)

    def _archive_and_delete(self, conn: Any, record: MailRecord) -> bool:
        """Archive *record*'s message via IMAP, then delete its local row.

        Shared by :meth:`_handle_archive` and :meth:`_handle_send_draft`.
        Computes the effective archive root + subfolder, performs the IMAP
        move (only when IMAP is configured and the record has a tracked
        UID), then removes the local database record.

        Returns ``True`` on success.  On a security-policy violation it
        sends a 400 and returns ``False``; on an IMAP/IO failure it sends a
        502 and returns ``False`` — in both error cases the local record is
        left intact.
        """
        from robotsix_auto_mail.db import delete_record_by_message_id

        # Compute the effective archive subfolder.
        subfolder = get_archive_subfolder(
            conn,
            record.message_id,
            record,
            api_key=self.mail_config.llm_api_key if self.mail_config else "",
        )

        # Determine the archive root.
        archive_root = (
            self.mail_config.archive_root
            if self.mail_config is not None
            else DEFAULT_ARCHIVE_ROOT
        )

        # Determine the namespace prefix (empty when unset).
        namespace = (
            self.mail_config.archive_namespace if self.mail_config is not None else ""
        )

        # Effective root: namespace + archive_root (user supplies
        # the delimiter as part of the namespace, e.g. "INBOX.").
        effective_root = namespace + archive_root

        # -- IMAP move phase (only when IMAP is configured and the
        #    record has a tracked UID) --
        if self.mail_config is not None and record.imap_uid is not None:
            from robotsix_auto_mail.imap import ImapError, ImapMessageNotFoundError

            try:
                self._imap_archive_move(
                    self.mail_config,
                    record.imap_uid,
                    effective_root,
                    subfolder,
                    source_folder=record.source_folder,
                    message_id=record.message_id,
                )
            except ValueError as exc:
                self._bad_request(str(exc))
                return False
            except ImapMessageNotFoundError:
                from robotsix_auto_mail.db import update_record_source
                from robotsix_auto_mail.imap import (
                    ImapClient,
                    cross_folder_resolve,
                )
                from robotsix_auto_mail.server.adapters import _archive_dest_folder

                try:
                    with ImapClient(self.mail_config) as client2:
                        cross = cross_folder_resolve(client2, record.message_id)
                        if cross is not None:
                            new_folder, new_uid = cross
                            update_record_source(
                                conn,
                                record.message_id,
                                source_folder=new_folder,
                                imap_uid=new_uid,
                            )
                            # Compute the archive destination.
                            delimiter = next(
                                (
                                    f.delimiter
                                    for f in client2.list_folders()
                                    if f.delimiter
                                ),
                                "/",
                            )
                            dest_folder = _archive_dest_folder(
                                effective_root, subfolder, delimiter
                            )
                            if dest_folder is None:
                                raise ValueError(
                                    "Archive destination escapes archive root"
                                )
                            # Ensure destination hierarchy exists.
                            parts = dest_folder.split(delimiter)
                            for i in range(1, len(parts) + 1):
                                client2.create_folder(delimiter.join(parts[:i]))
                            client2.move_message(new_uid, dest_folder)
                        # Mail gone or healed — delete the local record
                        # in both cases.
                        delete_record_by_message_id(conn, record.message_id)
                        return True
                except ValueError as exc:
                    self._bad_request(str(exc))
                    return False
                except (ImapError, OSError) as exc:
                    self._send_response(
                        f"IMAP cross-folder resolution failed: {exc}",
                        status=502,
                    )
                    return False
            except (ImapError, OSError) as exc:
                self._send_response(
                    f"IMAP archive failed: {exc}",
                    status=502,
                )
                return False

        # -- record the human-confirmed archive-folder choice (best-effort),
        #    BEFORE the local row is deleted so the memory survives --
        if subfolder:
            with contextlib.suppress(Exception):
                # Non-fatal: memory is advisory only
                record_archive_folder_choice(conn, record, subfolder)

        # -- local DB cleanup --
        delete_record_by_message_id(conn, record.message_id)
        return True

    def _handle_archive(self) -> None:
        """Process POST /archive — move mail to archive folder via IMAP
        and remove it from the local database.
        """

        def archive_action(conn: Any, record: MailRecord, redirect_to: str) -> bool:
            return self._archive_and_delete(conn, record)

        self._handle_post_action(
            "message_id",
            "redirect_to",
            action=archive_action,
        )

    def _handle_save_notes(self) -> None:
        """Process POST /save-notes — persist notes for a mail record."""
        from robotsix_auto_mail.db import update_notes

        def save_notes_action(
            conn: Any, record: MailRecord, redirect_to: str, notes: str
        ) -> bool:
            update_notes(conn, record.message_id, notes)
            return True

        self._handle_post_action(
            "message_id",
            "redirect_to",
            "notes",
            no_strip=frozenset({"notes"}),
            action=save_notes_action,
        )
