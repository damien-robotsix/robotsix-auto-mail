"""Unit tests for ``_redirect_generate_draft``.

Drives the mixin directly against a mock handler *self*, covering safe
and unsafe redirect targets.
"""

from __future__ import annotations

from tests.server._test_helpers import _DraftMixinFakeHandler


class TestRedirectGenerateDraft:
    """Tests for ``_redirect_generate_draft``."""

    def test_safe_redirect_to_used(self, tmp_db_path: str) -> None:
        handler = _DraftMixinFakeHandler(tmp_db_path)
        handler._redirect_generate_draft("msg-1", "/detail?msg=msg-1")
        handler._redirect.assert_called_once_with("/detail?msg=msg-1", 302)

    def test_unsafe_redirect_to_falls_back_to_board(self, tmp_db_path: str) -> None:
        handler = _DraftMixinFakeHandler(tmp_db_path)
        handler._redirect_generate_draft("msg-2", "//evil.com/phish")
        handler._redirect.assert_called_once_with("/board#msg-2", 302)

    def test_empty_redirect_to_falls_back_to_board(self, tmp_db_path: str) -> None:
        handler = _DraftMixinFakeHandler(tmp_db_path)
        handler._redirect_generate_draft("msg-3", "")
        handler._redirect.assert_called_once_with("/board#msg-3", 302)
