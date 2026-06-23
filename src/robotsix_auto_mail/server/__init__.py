"""HTTP server for the read-only kanban mail board.

Provides ``make_board_handler`` — a factory that returns a
``BaseHTTPRequestHandler`` subclass wired to a specific SQLite database
path.

The implementation is split across internal submodules:

- ``_constants`` — static assets, board columns, and leaf helpers.
- ``adapters`` — the ``render_board`` column adapter and the
  background triage runner.
- ``views`` — HTML/view renderers.
- ``handlers`` — the ``BoardHandler`` request handler and the
  ``make_board_handler`` factory.

This module re-exports the public symbols so
``from robotsix_auto_mail.server import ...`` keeps working unchanged.
"""

from __future__ import annotations

from robotsix_auto_mail.server.handlers import BoardHandler as BoardHandler
from robotsix_auto_mail.server.handlers import (
    make_board_handler as make_board_handler,
)

__all__ = [
    "BoardHandler",
    "make_board_handler",
]
