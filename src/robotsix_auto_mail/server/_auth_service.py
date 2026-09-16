"""Board-auth service — OAuth2 device-code flow from the board UI."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlparse

from robotsix_auto_mail.config import ConfigurationError
from robotsix_auto_mail.core._constants import _WATERMARK_IDLE
from robotsix_auto_mail.oauth2 import MICROSOFT_PROVIDER, device_code_login
from robotsix_auto_mail.server._services import Service

if TYPE_CHECKING:
    from robotsix_auto_mail.config import MailAccountsConfig
    from robotsix_auto_mail.server._board_handler_protocol import RequestContext


class AuthService(Service):
    """Stateless service providing OAuth2 device-code auth endpoints.

    The device-code flow state (``_AUTH_FLOWS`` / ``_AUTH_EVENTS``) is
    intentionally class-level: it must survive across the independent
    ``/auth-start`` and ``/auth-status`` requests that drive one login, so it
    is shared process-wide rather than stored per request.
    """

    #: Per-flow state keyed by ``flow_key`` (``account_id``).
    _AUTH_FLOWS: dict[str, dict[str, Any]] = {}  # noqa: RUF012

    #: Per-flow ``threading.Event`` so POST /auth-start can block until
    #: ``device_code_login`` fires the ``on_prompt`` callback.
    _AUTH_EVENTS: dict[str, threading.Event] = {}  # noqa: RUF012

    # -- POST /auth-start ---------------------------------------------------

    def handle_auth_start(self, ctx: RequestContext) -> None:
        """Start the OAuth2 device-code flow for *account_id*."""
        # 1. Read the URL-encoded POST body.
        length = int(ctx.headers.get("Content-Length", "0"))
        raw = ctx.rfile.read(length).decode("utf-8", errors="replace")
        body = parse_qs(raw)
        account_id = (body.get("account_id", [""])[0]).strip()

        # 2. Multi-account is the only model; an account id is mandatory.
        if not account_id:
            ctx._serve_json({"error": "No account id specified"}, status=400)
            return

        # 3. Resolve the MailConfig for the target account.
        accounts = cast("MailAccountsConfig | None", ctx.accounts)
        if accounts is None:
            ctx._serve_json({"error": "No accounts configured"}, status=400)
            return
        try:
            config = accounts.get(account_id).config
        except Exception as exc:
            ctx._serve_json({"error": f"Unknown account: {exc}"}, status=400)
            return

        # 4. Only Microsoft OAuth2 accounts are supported.
        if (
            config is None
            or getattr(config, "oauth2_provider", "") != MICROSOFT_PROVIDER
        ):
            ctx._serve_json(
                {"error": "Account is not configured for Microsoft OAuth2"},
                status=400,
            )
            return

        # 5. Compute the flow key.
        flow_key = account_id

        # 5. Idempotent guard — if a flow is already running, return the
        #    current state so the board JS can resume polling.
        existing = AuthService._AUTH_FLOWS.get(flow_key)
        if existing is not None and existing.get("status") in (
            "pending_prompt",
            "pending_consent",
        ):
            ctx._serve_json(existing)
            return

        # 6. Initialise state and event.
        AuthService._AUTH_FLOWS[flow_key] = {"status": "pending_prompt"}
        event = threading.Event()
        AuthService._AUTH_EVENTS[flow_key] = event

        # 7. on_prompt callback — fires when MSAL returns the device code.
        def on_prompt(flow: dict[str, Any]) -> None:
            AuthService._AUTH_FLOWS[flow_key] = {
                "status": "pending_consent",
                "message": flow.get("message", ""),
                "user_code": flow.get("user_code", ""),
                "verification_uri": flow.get("verification_uri", ""),
            }
            event.set()

        # 8. Background worker.
        def _run() -> None:
            try:
                device_code_login(config, on_prompt=on_prompt)
                # Auto-probe so the DB health row reflects the freshly-authorised
                # account before the page reloads.  Failure is non-fatal.
                try:
                    import logging

                    from robotsix_auto_mail.core.health import probe_account, utcnow
                    from robotsix_auto_mail.db.queries import write_account_health
                    from robotsix_auto_mail.server._constants import _with_db

                    status_val, error_val = probe_account(config)
                    with _with_db(config.db_path, skip_migrations=False) as conn:
                        write_account_health(
                            conn,
                            status=status_val,
                            error=error_val,
                            checked_at=utcnow(),
                        )
                except Exception as _probe_exc:
                    logging.getLogger("robotsix_auto_mail.server.auth").warning(
                        "post-auth health probe failed: %s", _probe_exc
                    )
                AuthService._AUTH_FLOWS[flow_key]["status"] = "success"
            except ConfigurationError as exc:
                event.set()  # unblock POST if on_prompt never fired
                AuthService._AUTH_FLOWS[flow_key] = {
                    "status": "error",
                    "error": str(exc),
                }
            except Exception as exc:
                event.set()
                AuthService._AUTH_FLOWS[flow_key] = {
                    "status": "error",
                    "error": f"device-code login failed: {exc}",
                }

        # 9. Start the background thread.
        threading.Thread(target=_run, daemon=True).start()

        # 10. Block until on_prompt fires (or timeout).
        event.wait(timeout=15)

        # 11. Respond with the current state.
        ctx._serve_json(
            AuthService._AUTH_FLOWS.get(flow_key, {"status": "pending_prompt"})
        )

    # -- GET /auth-status ---------------------------------------------------

    def handle_auth_status(self, ctx: RequestContext) -> None:
        """Poll the status of a running device-code flow."""
        # 1. Parse account_id from the query string.
        parsed = urlparse(ctx.path)
        qs = parse_qs(parsed.query)
        account_id = (qs.get("account_id", [""])[0]).strip()

        # 2. An account id is mandatory.
        if not account_id:
            ctx._serve_json({"status": _WATERMARK_IDLE})
            return

        # 3. Compute flow key.
        flow_key = account_id

        # 3. Look up state.
        state = AuthService._AUTH_FLOWS.get(flow_key)
        if state is None:
            ctx._serve_json({"status": _WATERMARK_IDLE})
            return

        # 4. One-shot clear on success.
        if state.get("status") == "success":
            AuthService._AUTH_FLOWS.pop(flow_key, None)
            AuthService._AUTH_EVENTS.pop(flow_key, None)

        ctx._serve_json(state)
