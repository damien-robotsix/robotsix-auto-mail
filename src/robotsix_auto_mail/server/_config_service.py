"""Config-sync and archive-proposal service for the board server."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from robotsix_auto_mail.server._request_helpers import handle_post_action
from robotsix_auto_mail.server._services import Service
from robotsix_auto_mail.triage import set_archive_subfolder_override

if TYPE_CHECKING:
    from robotsix_auto_mail.server._board_handler_protocol import RequestContext


class ConfigService(Service):
    """Stateless service providing config-sync and archive-proposal handlers."""

    def handle_config_sync(self, ctx: RequestContext) -> None:
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
            ctx._problem(
                status=503,
                kind="config-sync-unavailable",
                title="Config-sync Unavailable",
                detail=(
                    "Config-sync advisory requires the optional LLM "
                    "extra, which is not installed"
                ),
            )
            return

        from robotsix_auto_mail.server._constants import _with_db

        try:
            with _with_db(ctx.db_path, skip_migrations=False) as conn:
                result = run_config_sync_agent(conn=conn)
        except ConfigSyncError as exc:
            ctx._problem(
                status=503,
                kind="config-sync-failed",
                title="Config-sync Failed",
                detail=str(exc),
            )
            return
        except Exception as exc:
            ctx._problem(
                status=503,
                kind="config-sync-failed",
                title="Config-sync Failed",
                detail=str(exc),
            )
            return

        ctx._serve_json(result.model_dump(), status=200)

    def handle_archive_proposal(self, ctx: RequestContext) -> None:
        """Process POST /archive-proposal — store a user override and redirect."""

        def archive_proposal_action(
            conn: Any, record: Any, redirect_to: str, subfolder: str
        ) -> bool:
            if subfolder:
                if subfolder.startswith("/"):
                    ctx._bad_request("Subfolder must not be an absolute path")
                    return False
                if any(segment == ".." for segment in subfolder.split("/")):
                    ctx._bad_request("Subfolder must not contain '..' segments")
                    return False
                if len(subfolder) > 256:
                    ctx._bad_request(
                        "Subfolder exceeds maximum length of 256 characters"
                    )
                    return False

            set_archive_subfolder_override(conn, record.message_id, subfolder)
            return True

        handle_post_action(
            ctx,
            "message_id",
            "subfolder",
            "redirect_to",
            action=archive_proposal_action,
        )
