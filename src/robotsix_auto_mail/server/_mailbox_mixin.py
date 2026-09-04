"""Read-only mailbox enumeration + server-side search mixin for the board server.

Exposes two read-only endpoints over the chat HTTP API:

- ``GET /folders?account=<id>`` — enumerate the complete IMAP folder tree for
  an account (INBOX, provider special-use folders such as Gmail ``[Gmail]/All
  Mail``, and any custom folders/labels), with ``STATUS`` message counts where
  cheaply available.
- ``GET /search?account=<id>&from=&subject=&text=&since=&before=&folder=
  &has_attachments=&limit=`` — map keyword criteria to an IMAP ``SEARCH`` and
  return matching message headers (optionally across **all** selectable
  folders), tagging each result with its folder and an attachment summary.

Strictly read-only — no send, move, delete, or flag mutation happens here.
This is v1: keyword IMAP ``SEARCH`` only.  Semantic / embedding-based search is
a possible v2 follow-up and is deliberately out of scope.
"""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import datetime as _datetime
import email.utils
import json
import logging
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

if TYPE_CHECKING:
    from robotsix_auto_mail.imap import ImapClient

logger = logging.getLogger(__name__)

#: Default / cap for ``GET /search`` results (newest-first).  The cap bounds
#: the number of full-body fetches needed to derive attachment summaries, so
#: it must stay small.
_SEARCH_DEFAULT_LIMIT = 100
_SEARCH_MAX_LIMIT = 500


def _escape_search(value: str) -> str:
    """Escape ``\\`` and ``"`` inside an IMAP SEARCH quoted-string value."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


class _MailboxMixin:
    """Mixin providing read-only ``GET /folders`` and ``GET /search``."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

        self: BoardHandlerProtocol

    # -- GET /folders ------------------------------------------------------

    def _serve_folders(self) -> None:
        """Serve GET /folders?account=<id> — enumerate every IMAP folder.

        Returns one entry per folder with its ``name`` (full IMAP path),
        ``delimiter``, ``flags`` (e.g. ``\\HasChildren``, ``\\Noselect``) and,
        where cheaply available, ``messages`` / ``unseen`` counts from IMAP
        ``STATUS``.  Includes INBOX, provider special-use folders (Gmail
        ``[Gmail]/All Mail``, ``[Gmail]/Sent Mail``, …) and any custom
        folders/labels.  Read-only — no side effects.

        Short-circuits in aggregate (``?account=__all__``) mode: there is no
        single IMAP account to enumerate, so it returns 400 rather than leak
        whichever DB the request happens to point at.
        """
        if self._aggregate:
            self._serve_json(
                {"error": "folders is per-account; use ?account=<id>"},
                status=400,
            )
            return
        if not self._require_imap_configured():
            return

        from robotsix_auto_mail.imap import ImapClient, ImapError

        try:
            with ImapClient(self.mail_config) as client:
                all_folders = client.list_folders()
                delimiter = next((f.delimiter for f in all_folders if f.delimiter), "/")
                folders: list[dict[str, object]] = []
                for f in sorted(all_folders, key=lambda item: item.name):
                    entry: dict[str, object] = {
                        "name": f.name,
                        "flags": list(f.attributes),
                        "delimiter": f.delimiter,
                    }
                    try:
                        messages, unseen = client.status_folder(f.name)
                    except ImapError:
                        # STATUS unsupported / rejected for this mailbox —
                        # counts are "cheaply available" only when the server
                        # cooperates.  Omit them rather than fail the listing.
                        messages = unseen = None
                    if messages is not None:
                        entry["messages"] = messages
                    if unseen is not None:
                        entry["unseen"] = unseen
                    folders.append(entry)
        except ImapError as exc:
            self._send_response(f"IMAP error listing folders: {exc}", status=502)
            return
        except OSError as exc:
            self._send_response(f"IMAP connection error: {exc}", status=502)
            return

        self._serve_json(
            {
                "account": self._current_account_id or "main",
                "delimiter": delimiter,
                "folders": folders,
            }
        )

    # -- GET /search -------------------------------------------------------

    def _serve_search(self) -> None:
        """Serve GET /search?account=<id>&... — server-side IMAP search.

        Query params (all optional except ``account``):

        - ``from`` — sender substring (IMAP ``FROM``).
        - ``subject`` — subject substring (IMAP ``SUBJECT``).
        - ``text`` — free-text body/header match (IMAP ``TEXT``).
        - ``since`` / ``before`` — ISO date bounds (IMAP ``SINCE``/``BEFORE``).
        - ``folder`` — restrict to one folder path; when omitted, search all
          selectable folders and tag each result with its ``folder``.
        - ``has_attachments`` — optional boolean filter (best-effort,
          post-filtered by the actual parsed attachments).
        - ``limit`` — max results (default 100, capped 500), newest-first.

        Multiple criteria combine with AND.  Errors: 400 on malformed date or
        no criteria supplied, 404 unknown account / folder, 502 on IMAP error.
        Read-only — no side effects.
        """
        if self._aggregate:
            self._serve_json(
                {"error": "search is per-account; use ?account=<id>"},
                status=400,
            )
            return
        if not self._require_imap_configured():
            return

        qs = parse_qs(urlsplit(self.path).query)
        try:
            criteria, charset = self._build_search_criteria(qs)
        except ValueError as exc:
            self._bad_request(str(exc))
            return

        limit = self._parse_limit(qs)
        folder_values = qs.get("folder")
        folder_param = folder_values[0] if folder_values else None
        has_attachments = self._parse_bool_param(qs, "has_attachments")

        from robotsix_auto_mail.imap import ImapClient, ImapError

        try:
            with ImapClient(self.mail_config) as client:
                folders = self._resolve_search_folders(client, folder_param)
                if folders is None:
                    self._not_found()
                    return

                # folder -> matching uids
                folder_uids: dict[str, list[int]] = {}
                for folder in folders:
                    client.select_folder(folder)
                    uids = client.search_uids(criteria, charset=charset)
                    if uids:
                        folder_uids[folder] = uids

                # Fetch envelope headers per folder, tagging each with its folder.
                results: list[dict[str, object]] = []
                for folder, uids in folder_uids.items():
                    client.select_folder(folder)
                    for env in client.fetch_envelopes(uids):
                        env["folder"] = folder
                        results.append(env)

                # Newest-first, then apply the result cap.
                results.sort(key=self._sort_key, reverse=True)
                results = results[:limit]

                # Attachment summaries (best-effort) + has_attachments filter.
                results = self._attach_search_attachments(
                    client, results, has_attachments
                )
        except ImapError as exc:
            self._send_response(f"IMAP error searching mail: {exc}", status=502)
            return
        except OSError as exc:
            self._send_response(f"IMAP connection error: {exc}", status=502)
            return

        self._serve_json(
            {
                "account": self._current_account_id or "main",
                "count": len(results),
                "messages": results,
            }
        )

    # -- helpers -----------------------------------------------------------

    def _build_search_criteria(
        self, qs: dict[str, list[str]]
    ) -> tuple[str, str | None]:
        """Build an IMAP SEARCH criteria string (AND-combined) from *qs*.

        Returns ``(criteria, charset)`` where *charset* is ``"UTF-8"`` when any
        search value contains non-ASCII characters (so the server can match
        them) and ``None`` otherwise.

        Raises:
            ValueError: When no search criterion is supplied, or a date is
                malformed — callers map this to a 400.
        """
        parts: list[str] = []
        has_any = False
        has_non_ascii = False

        def _add(keyword: str, value: str) -> None:
            nonlocal has_any, has_non_ascii
            if not value:
                return
            has_any = True
            if any(ord(ch) > 127 for ch in value):
                has_non_ascii = True
            parts.append(f'{keyword} "{_escape_search(value)}"')

        for keyword, key in (
            ("FROM", "from"),
            ("SUBJECT", "subject"),
            ("TEXT", "text"),
        ):
            values = qs.get(key)
            if values:
                _add(keyword, values[0])

        since_values = qs.get("since")
        if since_values:
            has_any = True
            day = self._parse_search_date(since_values[0])
            parts.append(f"SINCE {day:%d-%b-%Y}")
        before_values = qs.get("before")
        if before_values:
            has_any = True
            day = self._parse_search_date(before_values[0])
            parts.append(f"BEFORE {day:%d-%b-%Y}")

        if not has_any:
            raise ValueError(
                "No search criteria supplied; provide at least one of "
                "from, subject, text, since, before"
            )

        charset = "UTF-8" if has_non_ascii else None
        return " ".join(parts), charset

    @staticmethod
    def _parse_search_date(value: str) -> _datetime.date:
        """Parse an ISO date (``YYYY-MM-DD``, optionally with time) to a date.

        Raises:
            ValueError: When *value* is not a valid ISO date.
        """
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return _datetime.datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Malformed date: {value!r} (expected ISO YYYY-MM-DD)")

    @staticmethod
    def _parse_limit(qs: dict[str, list[str]]) -> int:
        """Parse ``?limit=`` (default 100, capped at 500); bad input → default."""
        values = qs.get("limit")
        if not values:
            return _SEARCH_DEFAULT_LIMIT
        try:
            return min(max(int(values[0]), 1), _SEARCH_MAX_LIMIT)
        except (ValueError, TypeError):  # fmt: skip
            return _SEARCH_DEFAULT_LIMIT

    @staticmethod
    def _parse_bool_param(qs: dict[str, list[str]], key: str) -> bool | None:
        """Parse an optional boolean query param; ``None`` when absent/ambiguous."""
        values = qs.get(key)
        if not values:
            return None
        normalized = values[0].strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off"):
            return False
        return None

    @staticmethod
    def _resolve_search_folders(
        client: ImapClient, folder_param: str | None
    ) -> list[str] | None:
        """Resolve the folder(s) to search.

        When *folder_param* is given, returns ``[name]`` when that exact folder
        exists and ``None`` (→ 404) otherwise.  When omitted, returns every
        selectable folder (skipping ``\\Noselect`` container nodes).
        """
        all_folders = client.list_folders()
        if folder_param:
            for f in all_folders:
                if f.name == folder_param:
                    return [f.name]
            return None
        return [
            f.name
            for f in all_folders
            if not any(attr.lower() == "\\noselect" for attr in f.attributes)
        ]

    @staticmethod
    def _sort_key(env: dict[str, object]) -> _datetime.datetime:
        """Return a sortable timestamp for newest-first ordering."""
        date = env.get("date") or env.get("internal_date") or ""
        try:
            dt = email.utils.parsedate_to_datetime(str(date))
        except (ValueError, TypeError, OverflowError):  # fmt: skip
            dt = None
        if dt is not None:
            # ``parsedate_to_datetime()`` returns a NAIVE datetime when the
            # Date header carries no timezone offset but an AWARE one when it
            # does (and the epoch fallback below is aware UTC).  A result set
            # mixing tz-less and tz-aware dates would make ``sort()`` raise
            # TypeError (offset-naive vs offset-aware), so normalise every
            # parsed date to aware UTC before ordering.
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_datetime.UTC)
            return dt
        # Fall back to epoch so ordering stays deterministic.
        return _datetime.datetime.min.replace(tzinfo=_datetime.UTC)

    def _attach_search_attachments(
        self,
        client: ImapClient,
        results: list[dict[str, object]],
        has_attachments: bool | None,
    ) -> list[dict[str, object]]:
        """Populate each result's ``attachments`` summary and filter on it.

        Fetches the (bounded) result messages and parses each via
        ``parse_message`` to derive ``[{"filename", "mime_type", "size"}, …]``.
        When *has_attachments* is set, drops results that do not match.
        """
        by_folder: dict[str, list[int]] = {}
        for r in results:
            folder = r.get("folder")
            uid = r.get("uid")
            if not isinstance(folder, str) or not isinstance(uid, int):
                continue
            by_folder.setdefault(folder, []).append(uid)

        from robotsix_auto_mail.pipeline._parse import ParseError, parse_message

        attachments_by_key: dict[tuple[str, int], list[dict[str, object]]] = {}
        for folder, uids in by_folder.items():
            client.select_folder(folder)
            fetched = client.fetch_messages(uids)
            for uid, raw in fetched:
                try:
                    record = parse_message(raw, imap_uid=uid, source_folder=folder)
                except ParseError:
                    continue
                try:
                    attachments_by_key[(folder, uid)] = json.loads(
                        record.attachments_json
                    )
                except (json.JSONDecodeError, TypeError):  # fmt: skip
                    attachments_by_key[(folder, uid)] = []

        for r in results:
            folder = r.get("folder")
            uid = r.get("uid")
            key = (
                (folder, uid)
                if isinstance(folder, str) and isinstance(uid, int)
                else None
            )
            r["attachments"] = (
                attachments_by_key.get(key, []) if key is not None else []
            )

        if has_attachments is not None:
            results = [r for r in results if bool(r["attachments"]) == has_attachments]
        return results
