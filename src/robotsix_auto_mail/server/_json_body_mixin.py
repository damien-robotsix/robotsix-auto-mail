"""Shared JSON request-body parsing helper for board server mixins."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any


class _JsonBodyMixin:
    """Mixin providing shared JSON request-body parsing for handlers."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    def _read_json_object_body(
        self,
        *,
        allow_empty: bool = False,
        object_error: str = "JSON body must be an object",
    ) -> dict[str, Any] | None:
        """Read the request body and parse it as a JSON object.

        Reads ``Content-Length`` bytes, parses them as JSON, and returns
        the resulting ``dict``.  On failure it sends the appropriate
        ``_bad_request`` response and returns ``None`` — callers should
        ``return`` immediately when this returns ``None``.

        When *allow_empty* is true, an empty (or whitespace-only) body
        parses to an empty ``dict`` instead of being rejected as malformed
        JSON.  *object_error* is the detail message used when the body
        parses to a JSON value that is not an object.
        """
        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8") if content_length else ""
        if allow_empty and not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self._bad_request("Malformed JSON body")
            return None
        if not isinstance(data, dict):
            self._bad_request(object_error)
            return None
        return data
