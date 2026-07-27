"""Unit tests for ``_handle_generate_draft``.

Drives the mixin directly against a mock handler *self*, isolating the
logic from the HTTP transport and covering branches that integration
tests miss (ImportError degradation, DraftGenerationError swallowing,
etc.).
"""

from __future__ import annotations

import sys
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from tests.server._test_helpers import _DraftMixinFakeHandler
from tests.server.conftest_helpers import _populate_db


class TestHandleGenerateDraft:
    """Tests for ``_handle_generate_draft``."""

    def _setup_handler(
        self, db_path: str, message_id: str, redirect_to: str = "/board"
    ) -> _DraftMixinFakeHandler:
        handler = _DraftMixinFakeHandler(
            db_path,
            mail_config=MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="me@example.com",
                password="s3cret",
                llm_api_key="sk-test",
            ),
        )
        handler.headers.get.return_value = 200
        handler.rfile.read.return_value = (
            f"message_id={message_id}&redirect_to={redirect_to}"
        ).encode("utf-8")
        return handler

    def test_import_error_degradation(self, tmp_db_path: str) -> None:
        """When robotsix_auto_mail.server._draft_generator is not importable, redirect gracefully.

        Uses ``mock.patch.dict`` to set the module entry to ``None`` in
        ``sys.modules``, which forces Python to raise ``ImportError``
        (as if the optional extra were not installed) rather than
        attempting a re-import that would likely succeed because its
        dependencies are core packages.
        """
        handler = self._setup_handler(tmp_db_path, "any-id")
        handler._redirect_generate_draft = mock.MagicMock()

        with (
            mock.patch.dict(
                sys.modules, {"robotsix_auto_mail.server._draft_generator": None}
            ),
            mock.patch(
                "robotsix_auto_mail.server._draft_mixin.set_triage_decision"
            ) as mock_set,
            mock.patch("robotsix_auto_mail.server._constants.init_db") as mock_init_db,
        ):
            handler._handle_generate_draft()

        # The ImportError path must redirect but NOT open a DB connection
        # or set a triage decision (both happen only after a successful
        # draft import).
        handler._redirect_generate_draft.assert_called_once_with("any-id", "/board")
        mock_init_db.assert_not_called()
        mock_set.assert_not_called()

    def test_draft_generation_error_swallowed(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "gen-err",
                    "sender": "x@x.com",
                    "subject": "Test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = self._setup_handler(single_db, "gen-err")
        handler._redirect_generate_draft = mock.MagicMock()

        from robotsix_auto_mail.server._draft_generator import DraftGenerationError

        with (
            mock.patch(
                "robotsix_auto_mail.server._draft_generator.generate_draft_reply",
                side_effect=DraftGenerationError("LLM unavailable"),
            ),
            mock.patch(
                "robotsix_auto_mail.server._draft_mixin.set_triage_decision"
            ) as mock_set,
        ):
            handler._handle_generate_draft()

        # set_triage_decision should NOT be called (error was swallowed).
        mock_set.assert_not_called()
        # Still redirects.
        handler._redirect_generate_draft.assert_called_once_with("gen-err", "/board")

    def test_success_sets_draft_ready(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "gen-ok",
                    "sender": "x@x.com",
                    "subject": "Test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = self._setup_handler(single_db, "gen-ok")
        handler._redirect_generate_draft = mock.MagicMock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._draft_generator.generate_draft_reply"
            ),
            mock.patch(
                "robotsix_auto_mail.server._draft_mixin.set_triage_decision"
            ) as mock_set,
        ):
            handler._handle_generate_draft()

        mock_set.assert_called_once_with(
            mock.ANY, "gen-ok", "DRAFT_READY", source="user", reason="draft generated"
        )
        handler._redirect_generate_draft.assert_called_once_with("gen-ok", "/board")

    def test_missing_message_id_returns_400(self, tmp_db_path: str) -> None:
        handler = _DraftMixinFakeHandler(tmp_db_path)
        handler.headers.get.return_value = 100
        handler.rfile.read.return_value = b"message_id=&redirect_to=/board"

        handler._handle_generate_draft()
        handler._bad_request.assert_called_once_with("Missing message_id")
