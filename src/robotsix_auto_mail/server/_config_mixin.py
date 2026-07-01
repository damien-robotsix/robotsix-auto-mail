"""Config-sync and archive-proposal mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

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

        from robotsix_auto_mail.server._constants import _with_db

        try:
            with _with_db(self.db_path, skip_migrations=False) as conn:
                result = run_config_sync_agent(conn=conn)
        except ConfigSyncError as exc:
            self._serve_json({"error": str(exc)}, status=503)
            return
        except Exception as exc:
            self._serve_json({"error": str(exc)}, status=503)
            return

        self._serve_json(result.model_dump(), status=200)

    def _handle_archive_proposal(self) -> None:
        """Process POST /archive-proposal — store a user override and redirect."""

        def archive_proposal_action(
            conn: Any, record: Any, redirect_to: str, subfolder: str
        ) -> bool:
            if subfolder:
                if subfolder.startswith("/"):
                    self._bad_request("Subfolder must not be an absolute path")
                    return False
                if any(segment == ".." for segment in subfolder.split("/")):
                    self._bad_request("Subfolder must not contain '..' segments")
                    return False
                if len(subfolder) > 256:
                    self._bad_request(
                        "Subfolder exceeds maximum length of 256 characters"
                    )
                    return False

            set_archive_subfolder_override(conn, record.message_id, subfolder)
            # -- record the human-confirmed folder choice (best-effort);
            #    an empty subfolder (clearing the override) records nothing --
            if subfolder:
                with contextlib.suppress(Exception):
                    # Non-fatal: memory is advisory only
                    record_archive_folder_choice(conn, record, subfolder)
            return True

        self._handle_post_action(
            "message_id",
            "subfolder",
            "redirect_to",
            action=archive_proposal_action,
        )
