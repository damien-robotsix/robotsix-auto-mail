"""Config-sync and archive-proposal mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from robotsix_auto_mail.triage import (
    record_archive_folder_choice,
    set_archive_subfolder_override,
)


class _ConfigMixin:
    """Mixin providing config-sync and archive-proposal handlers."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    def _handle_config_sync(self) -> None:
        """Process POST /config-sync — run the LLM drift advisory agent.

        Lazily imports the optional LLM-backed agent so the rest of the
        server works without ``pydantic_ai`` installed.  On success,
        returns the ``ConfigSyncResult`` serialized as JSON; on a missing
        optional extra returns 503, and on any agent failure returns 503
        with a JSON error body (never a traceback).
        """
        try:
            from robotsix_auto_mail.config.config_sync_agent import (
                ConfigSyncError,
                run_config_sync_agent,
            )
        except ImportError:
            self._serve_json(
                {
                    "error": (
                        "Config-sync advisory requires the optional LLM "
                        "extra, which is not installed"
                    )
                },
                status=503,
            )
            return

        from robotsix_auto_mail.db import init_db

        conn = init_db(self.db_path)
        try:
            result = run_config_sync_agent(conn=conn)
        except ConfigSyncError as exc:
            self._serve_json({"error": str(exc)}, status=503)
            return
        except Exception as exc:
            self._serve_json({"error": str(exc)}, status=503)
            return
        finally:
            conn.close()

        self._serve_json(result.model_dump(), status=200)

    def _handle_archive_proposal(self) -> None:
        """Process POST /archive-proposal — store a user override and redirect."""
        from robotsix_auto_mail.db import get_record_by_message_id, init_db

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()
        subfolder = (fields.get("subfolder") or [""])[0].strip()

        if not message_id:
            self._bad_request("Missing message_id")
            return

        if subfolder:
            if subfolder.startswith("/"):
                self._bad_request("Subfolder must not be an absolute path")
                return
            if any(segment == ".." for segment in subfolder.split("/")):
                self._bad_request("Subfolder must not contain '..' segments")
                return
            if len(subfolder) > 256:
                self._bad_request("Subfolder exceeds maximum length of 256 characters")
                return

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            set_archive_subfolder_override(conn, message_id, subfolder)
            # -- record the human-confirmed folder choice (best-effort);
            #    an empty subfolder (clearing the override) records nothing --
            if subfolder:
                try:
                    record = get_record_by_message_id(conn, message_id)
                    if record is not None:
                        record_archive_folder_choice(conn, record, subfolder)
                except Exception:  # noqa: S110  # nosec B110
                    pass  # Non-fatal: memory is advisory only
        finally:
            conn.close()

        self._redirect("/board", code=302)
