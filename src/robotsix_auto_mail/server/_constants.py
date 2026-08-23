"""Leaf constants and small helpers for the board server.

This module must not import from any other ``robotsix_auto_mail.server``
submodule — it is the bottom of the server dependency DAG.
"""

from __future__ import annotations

import importlib.resources
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from robotsix_auto_mail.db import init_db
from robotsix_auto_mail.triage import (
    TRIAGE_ACTION_ORDER,
)


@contextmanager
def _with_db(
    db_path: str, *, skip_migrations: bool = True
) -> Iterator[sqlite3.Connection]:
    """Open a DB connection, yield it, and close it in a finally block.

    All board-server mixins use this helper so the connection lifecycle
    (``init_db`` / ``conn.close()``) is defined once.
    """
    conn = init_db(db_path, skip_migrations=skip_migrations)
    try:
        yield conn
    finally:
        conn.close()


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
_STATIC_AUTOMAIL_BOARD_CSS = (  # lgtm[py/unused-global-variable]
    importlib.resources.files("robotsix_auto_mail.server") / "static" / "board.css"
).read_text()
# Auto-mail's app-layer JS overlay (board.js composer).  Served at
# /static/board-auto-mail.js so it sits alongside the library's board.js.
_STATIC_BOARD_AUTOMAIL_JS = (  # lgtm[py/unused-global-variable]
    importlib.resources.files("robotsix_auto_mail.server")
    / "static"
    / "board-auto-mail.js"
).read_text()
# CSP-safe event delegation layer (replaces inline onclick/onsubmit/onchange).
_STATIC_BOARD_EVENTS_JS = (  # lgtm[py/unused-global-variable]
    importlib.resources.files("robotsix_auto_mail.server")
    / "static"
    / "board-events.js"
).read_text()

# CSP-safe parent-frame redirect for the add-account embed page
# (replaces inline <script>window.top.location.href=…</script>).
_STATIC_ADD_ACCOUNT_REDIRECT_JS = (  # lgtm[py/unused-global-variable]
    importlib.resources.files("robotsix_auto_mail.server")
    / "static"
    / "add-account-redirect.js"
).read_text()

# CSP-safe Settings panel bootstrap (replaces inline <script type="module">).
_STATIC_SETTINGS_LOADER_JS = (  # lgtm[py/unused-global-variable]
    importlib.resources.files("robotsix_auto_mail.server")
    / "static"
    / "settings-loader.js"
).read_text()

# CSP-safe AppShell bootstrap (replaces the bespoke board-header with the
# shared @robotsix/ui AppShell — robotsix-standards ui-shell.md).
_STATIC_APPSHELL_LOADER_JS = (  # lgtm[py/unused-global-variable]
    importlib.resources.files("robotsix_auto_mail.server")
    / "static"
    / "appshell-loader.js"
).read_text()

# Chat-access SKILL.md — served at GET /chat-skill per the
# robotsix-standards chat-access standard §1.
_STATIC_CHAT_SKILL_MD = (  # lgtm[py/unused-global-variable]
    importlib.resources.files("robotsix_auto_mail.server") / "static" / "skill.md"
).read_text()


def _read_vendored_ui(name: str) -> str | None:
    """Read a vendored ``@robotsix/ui`` build artifact, or ``None`` if absent.

    These two files are not committed — they are build output, copied in from
    the pinned package at image build time (see the ``ui`` stage in the
    Dockerfile) or by ``scripts/vendor-ui.sh`` for a local checkout.  A missing
    file must degrade the Settings page, never break the import.
    """
    try:
        return (
            importlib.resources.files("robotsix_auto_mail.server") / "static" / name
        ).read_text()
    except FileNotFoundError, OSError:
        return None


#: The shared config panel and its stylesheet — the fleet's one settings
#: renderer (robotsix-standards config-ownership.md, "cross-UI uniformity").
_STATIC_ROBOTSIX_UI_JS = _read_vendored_ui(  # lgtm[py/unused-global-variable]
    "robotsix-ui.js"
)
_STATIC_ROBOTSIX_UI_CSS = _read_vendored_ui(  # lgtm[py/unused-global-variable]
    "robotsix-ui.css"
)

# -- Constants --------------------------------------------------------------
_BOARD_COLUMNS = TRIAGE_ACTION_ORDER  # lgtm[py/unused-global-variable]

#: Reserved sentinel account id that selects the aggregate (all-accounts)
#: board view.  Must not be used as a real ``account_id`` — collisions
#: with a real account named ``__all__`` are out of scope.
GLOBAL_VIEW_ACCOUNT_ID: str = "__all__"

#: The set of valid batch-operation verbs used in the ``batch_op:state``
#: watermark and the board UI.  Adding a verb here automatically makes
#: it available to the JavaScript overlay via the ``#board-config`` JSON
#: payload so the two sides stay in sync without manual coordination.
BATCH_OP_VERBS: frozenset[str] = frozenset({"archive", "delete"})

#: Human-readable progressive-form labels for each batch-op verb,
#: keyed by verb.  Used by both the Python ``_batch_banner_html`` and
#: the JavaScript progress banner (via ``#board-config``) so that
#: adding a new verb to ``BATCH_OP_VERBS`` only requires adding its
#: label here — no ternary edits in either language.
BATCH_OP_VERB_LABELS: dict[str, str] = {
    "archive": "Archiving",
    "delete": "Deleting",
}


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
    return (
        location.startswith("/")
        and not location.startswith(("//", "/\\"))
        and not any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in location)
    )


# -- Referenced by other modules; silence py/unused-global-variable --
_ = (
    _STATIC_AUTOMAIL_BOARD_CSS,
    _STATIC_BOARD_AUTOMAIL_JS,
    _STATIC_BOARD_EVENTS_JS,
    _STATIC_ADD_ACCOUNT_REDIRECT_JS,
    _STATIC_SETTINGS_LOADER_JS,
    _STATIC_APPSHELL_LOADER_JS,
    _STATIC_CHAT_SKILL_MD,
    _STATIC_ROBOTSIX_UI_JS,
    _STATIC_ROBOTSIX_UI_CSS,
    _BOARD_COLUMNS,
)


def _update_handler_factory_cache(
    server: object,
    accounts: object,
    default_account_id: str | None = None,
) -> None:
    """Update the handler factory's functools.partial keywords dict
    so the next handler instance picks up the new config without
    a server restart."""
    handler_factory = getattr(server, "RequestHandlerClass", None)
    if handler_factory is not None and hasattr(handler_factory, "keywords"):
        kw = handler_factory.keywords
        if "accounts" in kw:
            kw["accounts"] = accounts
        if "default_account_id" in kw and default_account_id:
            kw["default_account_id"] = default_account_id


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
        except json.JSONDecodeError, TypeError, KeyError:
            # Malformed watermark JSON — fall back to the defaults
            # (empty folder set, "/" delimiter, archive_root) set above.
            pass
    return existing_folders, delimiter, effective_root
