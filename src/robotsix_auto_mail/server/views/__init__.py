"""HTML/view renderers for the board server.

Split across focused submodules:
- ``board`` — board rendering functions
- ``detail`` — email detail rendering functions
- ``forms`` — form renderers
"""

from __future__ import annotations

from robotsix_auto_mail.server.views.board import (
    _build_board_content as _build_board_content,
)
from robotsix_auto_mail.server.views.board import (
    _build_board_html as _build_board_html,
)
from robotsix_auto_mail.server.views.board import (
    _build_global_board_content as _build_global_board_content,
)
from robotsix_auto_mail.server.views.board import (
    _build_global_board_html as _build_global_board_html,
)
from robotsix_auto_mail.server.views.detail import (
    _build_detail_html as _build_detail_html,
)

__all__ = [
    "_build_board_content",
    "_build_board_html",
    "_build_detail_html",
    "_build_global_board_content",
    "_build_global_board_html",
]
