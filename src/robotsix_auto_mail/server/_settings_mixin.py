"""GET /settings and PUT /settings mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from robotsix_auto_mail.server._constants import _with_db


class _SettingsMixin:
    """Mixin providing per-component settings read/write endpoints."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    # -- GET /settings -------------------------------------------------------

    def _handle_get_settings(self) -> None:
        """Return all component settings as JSON with secrets masked.

        GET /settings → ``{"settings": {"imap_host": "…", "password": "***", …}}``

        The response includes a ``source`` field indicating whether the
        settings came from the internal store (``"internal"``) or were
        derived from the config file (``"config-file"`` — store was
        empty, no import has run yet).
        """
        from robotsix_auto_mail.settings import SettingsStore

        store = SettingsStore(self.db_path)
        with _with_db(self.db_path) as conn:
            if store.is_empty(conn):
                # No imported settings yet — derive from the in-memory
                # MailConfig (config file) with secrets masked.
                if self.mail_config is not None:
                    cfg = self.mail_config
                    # We can't iterate fields directly on the Protocol type,
                    # so use the concrete MailConfig fields.
                    from robotsix_auto_mail.config.model import MailConfig

                    from robotsix_auto_mail.settings.store import _masked_value

                    settings = {
                        field_name: _masked_value(
                            field_name,
                            str(getattr(cfg, field_name)),
                        )
                        for field_name in MailConfig.model_fields
                    }
                else:
                    settings = {}
                self._serve_json(
                    {"settings": settings, "source": "config-file"}, status=200
                )
                return

            settings = store.get_all(conn)

        self._serve_json(
            {"settings": settings, "source": "internal"}, status=200
        )

    # -- PUT /settings -------------------------------------------------------

    def _handle_put_settings(self) -> None:
        """Validate and apply partial settings updates.

        PUT /settings  (JSON body: ``{"imap_host": "new.example.com", …}``)

        Returns ``{"ok": true, "errors": {}}`` on success, or
        ``{"ok": false, "errors": {"bad_field": "error message", …}}``
        on validation failure.  On partial failure valid fields are
        still persisted (the ``errors`` dict lists only the rejected
        keys).
        """
        from robotsix_auto_mail.settings import SettingsStore

        # Read the raw request body.
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            self._serve_json(
                {"ok": False, "errors": {"_body": "empty request body"}},
                status=400,
            )
            return

        raw_body = self.rfile.read(length)
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            self._serve_json(
                {"ok": False, "errors": {"_body": f"invalid JSON: {exc}"}},
                status=400,
            )
            return

        if not isinstance(body, dict):
            self._serve_json(
                {"ok": False, "errors": {"_body": "expected a JSON object"}},
                status=400,
            )
            return

        store = SettingsStore(self.db_path)
        with _with_db(self.db_path) as conn:
            errors = store.update(conn, body)

        if errors:
            self._serve_json({"ok": False, "errors": errors}, status=422)
            return

        self._serve_json({"ok": True, "errors": {}}, status=200)
