"""Board-auth mixin — OAuth2 device-code flow from the board UI."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from robotsix_auto_mail.config import ConfigurationError
from robotsix_auto_mail.oauth2 import MICROSOFT_PROVIDER, device_code_login

if TYPE_CHECKING:
    from ._board_handler_protocol import BoardHandlerProtocol


class _BoardAuthMixin:
    """Mixin providing OAuth2 device-code auth endpoints for the board."""

    if TYPE_CHECKING:
        self: BoardHandlerProtocol

    #: Per-flow state keyed by ``flow_key`` (``account_id`` or ``"single"``).
    _AUTH_FLOWS: dict[str, dict[str, Any]] = {}  # noqa: RUF012

    #: Per-flow ``threading.Event`` so POST /auth-start can block until
    #: ``device_code_login`` fires the ``on_prompt`` callback.
    _AUTH_EVENTS: dict[str, threading.Event] = {}  # noqa: RUF012

    # -- POST /auth-start ---------------------------------------------------

    def _handle_auth_start(self) -> None:
        """Start the OAuth2 device-code flow for *account_id*."""
        # 1. Read the URL-encoded POST body.
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        body = parse_qs(raw)
        account_id = (body.get("account_id", [""])[0]).strip()

        # 2. Resolve the MailConfig for the target account.
        if self.accounts is not None:
            try:
                config = self.accounts.get(account_id).config
            except Exception as exc:
                self._serve_json({"error": f"Unknown account: {exc}"}, status=400)
                return
        else:
            config = self.mail_config
            account_id = ""  # ignored; flow_key becomes "single"

        # 3. Only Microsoft OAuth2 accounts are supported.
        if (
            config is None
            or getattr(config, "oauth2_provider", "") != MICROSOFT_PROVIDER
        ):
            self._serve_json(
                {"error": "Account is not configured for Microsoft OAuth2"},
                status=400,
            )
            return

        # 4. Compute the flow key.
        flow_key = account_id or "single"

        # 5. Idempotent guard — if a flow is already running, return the
        #    current state so the board JS can resume polling.
        existing = _BoardAuthMixin._AUTH_FLOWS.get(flow_key)
        if existing is not None and existing.get("status") in (
            "pending_prompt",
            "pending_consent",
        ):
            self._serve_json(existing)
            return

        # 6. Initialise state and event.
        _BoardAuthMixin._AUTH_FLOWS[flow_key] = {"status": "pending_prompt"}
        event = threading.Event()
        _BoardAuthMixin._AUTH_EVENTS[flow_key] = event

        # 7. on_prompt callback — fires when MSAL returns the device code.
        def on_prompt(flow: dict[str, Any]) -> None:
            _BoardAuthMixin._AUTH_FLOWS[flow_key] = {
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
                _BoardAuthMixin._AUTH_FLOWS[flow_key]["status"] = "success"
            except ConfigurationError as exc:
                event.set()  # unblock POST if on_prompt never fired
                _BoardAuthMixin._AUTH_FLOWS[flow_key] = {
                    "status": "error",
                    "error": str(exc),
                }
            except Exception as exc:
                event.set()
                _BoardAuthMixin._AUTH_FLOWS[flow_key] = {
                    "status": "error",
                    "error": f"device-code login failed: {exc}",
                }

        # 9. Start the background thread.
        threading.Thread(target=_run, daemon=True).start()

        # 10. Block until on_prompt fires (or timeout).
        event.wait(timeout=15)

        # 11. Respond with the current state.
        self._serve_json(
            _BoardAuthMixin._AUTH_FLOWS.get(flow_key, {"status": "pending_prompt"})
        )

    # -- GET /auth-status ---------------------------------------------------

    def _handle_auth_status(self) -> None:
        """Poll the status of a running device-code flow."""
        # 1. Parse account_id from the query string.
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        account_id = (qs.get("account_id", [""])[0]).strip()

        # 2. Compute flow key.
        flow_key = account_id or "single"

        # 3. Look up state.
        state = _BoardAuthMixin._AUTH_FLOWS.get(flow_key)
        if state is None:
            self._serve_json({"status": "idle"})
            return

        # 4. One-shot clear on success.
        if state.get("status") == "success":
            _BoardAuthMixin._AUTH_FLOWS.pop(flow_key, None)
            _BoardAuthMixin._AUTH_EVENTS.pop(flow_key, None)

        self._serve_json(state)
