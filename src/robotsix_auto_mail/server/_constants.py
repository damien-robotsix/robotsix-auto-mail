"""Leaf constants and small helpers for the board server.

This module must not import from any other ``robotsix_auto_mail.server``
submodule — it is the bottom of the server dependency DAG.
"""

from __future__ import annotations

import importlib.resources
import json

from robotsix_auto_mail.triage import (
    TRIAGE_ACTION_ORDER,
)

# -- Static assets from robotsix_board -------------------------------------
# Pre-loaded at module level so _serve_static never touches the filesystem.
_STATIC_BOARD_JS = (
    importlib.resources.files("robotsix_board") / "static" / "board.js"
).read_text()
_STATIC_BOARD_CSS = (
    importlib.resources.files("robotsix_board") / "static" / "board.css"
).read_text()
# Auto-mail's app-layer stylesheet, served at /static/automail/board.css so
# it does not collide with the library's /static/board.css.  Loaded after
# the library CSS so its rules cascade over the library defaults.
_STATIC_AUTOMAIL_BOARD_CSS = (
    importlib.resources.files("robotsix_auto_mail") / "static" / "board.css"
).read_text()

# -- Constants --------------------------------------------------------------
_BOARD_COLUMNS = TRIAGE_ACTION_ORDER

#: Reserved sentinel account id that selects the aggregate (all-accounts)
#: board view.  Must not be used as a real ``account_id`` — collisions
#: with a real account named ``__all__`` are out of scope.
GLOBAL_VIEW_ACCOUNT_ID: str = "__all__"


def _is_safe_redirect_path(location: str) -> bool:
    """Return ``True`` if *location* is a safe same-origin relative path.

    Rejects values that could be used for open-redirect or HTTP
    response-splitting attacks.  A safe value must:

    - start with a single ``/`` (a relative, same-origin path),
    - not start with ``//`` (protocol-relative URL → other origin),
    - not start with ``/\\`` (backslash trick some browsers treat as
      protocol-relative), and
    - contain no CR (``\\r``), LF (``\\n``), or other ASCII control
      characters (which could inject extra response headers).
    """
    if not location.startswith("/"):
        return False
    if location.startswith(("//", "/\\")):
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in location):
        return False
    return True


def _parse_archive_structure(
    raw: str | None, archive_root: str
) -> tuple[set[str], str, str]:
    """Parse the ``archive_structure`` watermark JSON.

    Returns ``(existing_folders, delimiter, effective_root)``.
    Falls back to ``(set(), "/", archive_root)`` when *raw* is None
    or cannot be parsed.
    """
    existing_folders: set[str] = set()
    delimiter: str = "/"
    effective_root: str = archive_root
    if raw is not None:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                # Old format: bare list of folder names.
                existing_folders = set(data)
                delimiter = "/"
                effective_root = data[0] if data else archive_root
            else:
                # New format: {"delimiter": ..., "folders": [...]}.
                existing_folders = set(data["folders"])
                delimiter = data.get("delimiter", "/")
                effective_root = data["folders"][0] if data["folders"] else archive_root
        except (json.JSONDecodeError, TypeError, KeyError):
            # Malformed watermark JSON — fall back to the defaults
            # (empty folder set, "/" delimiter, archive_root) set above.
            pass
    return existing_folders, delimiter, effective_root
