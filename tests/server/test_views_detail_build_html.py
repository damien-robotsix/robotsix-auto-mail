"""Unit tests for _build_detail_html."""

from __future__ import annotations

from unittest import mock

from robotsix_auto_mail.server.views.detail import _build_detail_html
from robotsix_auto_mail.triage import TriageDecision

from ._view_helpers import _make_record


class TestBuildDetailHtml:
    # _build_detail_html imports *inside* the function:
    #   from robotsix_auto_mail.db import get_record_by_message_id, init_db
    # and uses the *module-level* import of get_triage_decision from
    #   from robotsix_auto_mail.triage import get_triage_decision
    # So we patch the db module (for the local import) and the detail
    # module itself (for the already-imported get_triage_decision).
    _PATCH_TRIG = "robotsix_auto_mail.server.views.detail.get_triage_decision"

    def test_valid_record_returns_full_html(self):
        record = _make_record()
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._constants.init_db", return_value=fake_conn
            ) as mock_init,
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ) as mock_get,
            mock.patch(
                self._PATCH_TRIG,
                return_value=decision,
            ) as mock_triage,
        ):
            result = _build_detail_html(":memory:", record.message_id)

        assert result is not None
        assert "<!DOCTYPE html>" in result
        assert "<title>Mail: Test Subject</title>" in result
        assert "sender@example.com" in result
        assert "← Back to board" in result
        mock_init.assert_called_once_with(":memory:", skip_migrations=True)
        mock_get.assert_called_once_with(fake_conn, record.message_id)
        mock_triage.assert_called_once_with(fake_conn, record.message_id)

    def test_embed_true_returns_fragment(self):
        record = _make_record()
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._constants.init_db", return_value=fake_conn
            ),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(
                self._PATCH_TRIG,
                return_value=decision,
            ),
        ):
            result = _build_detail_html(":memory:", record.message_id, embed=True)

        assert result is not None
        assert "<!DOCTYPE html>" not in result
        assert '<link rel="stylesheet" href="/static/automail/board.css">' in result
        assert "refreshBoard" in result

    def test_record_none_returns_none(self):
        fake_conn = mock.Mock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._constants.init_db", return_value=fake_conn
            ),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=None,
            ),
            mock.patch(
                self._PATCH_TRIG,
                return_value=None,
            ),
        ):
            result = _build_detail_html(":memory:", "missing@id")

        assert result is None

    def test_malformed_recipients_json_graceful_fallback(self):
        record = _make_record(
            recipients_json="{invalid json",
            attachments_json='[{"filename": "a.pdf"}]',
        )
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._constants.init_db", return_value=fake_conn
            ),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(
                self._PATCH_TRIG,
                return_value=decision,
            ),
        ):
            result = _build_detail_html(":memory:", record.message_id)

        assert result is not None
        # Malformed recipients → fallback to empty, so "(none)" for To
        assert "<em>(none)</em>" in result

    def test_malformed_attachments_json_graceful_fallback(self):
        record = _make_record(
            recipients_json='{"to": ["a@b.com"], "cc": []}',
            attachments_json="{invalid",
        )
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._constants.init_db", return_value=fake_conn
            ),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(
                self._PATCH_TRIG,
                return_value=decision,
            ),
        ):
            result = _build_detail_html(":memory:", record.message_id)

        assert result is not None
        # Malformed attachments → fallback to "(none)"
        assert "<em>(none)</em>" in result

    def test_no_subject_shows_placeholder_in_title(self):
        record = _make_record(subject="")
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._constants.init_db", return_value=fake_conn
            ),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(
                self._PATCH_TRIG,
                return_value=decision,
            ),
        ):
            result = _build_detail_html(":memory:", record.message_id)

        assert result is not None
        assert "(no subject)" in result

    def test_triage_decision_none_shows_placeholder(self):
        record = _make_record()
        fake_conn = mock.Mock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._constants.init_db", return_value=fake_conn
            ),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(
                self._PATCH_TRIG,
                return_value=None,
            ),
        ):
            result = _build_detail_html(":memory:", record.message_id)

        assert result is not None
        assert "(no triage decision)" in result

    # -- account-aware output tests ----------------------------------------

    def test_legacy_no_account_has_plain_move_action(self):
        """Without *current_account_id*, the move form action is ``/move``."""
        record = _make_record()
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._constants.init_db", return_value=fake_conn
            ),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(self._PATCH_TRIG, return_value=decision),
        ):
            result = _build_detail_html(":memory:", record.message_id)

        assert result is not None
        assert 'action="/move"' in result
        assert "?account=" not in result

    def test_real_account_adds_query_to_move_action(self):
        """A real *current_account_id* adds ``?account=<id>`` to the form."""
        record = _make_record()
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._constants.init_db", return_value=fake_conn
            ),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(self._PATCH_TRIG, return_value=decision),
        ):
            result = _build_detail_html(
                ":memory:", record.message_id, current_account_id="acct-42"
            )

        assert result is not None
        assert 'action="/move?account=acct-42"' in result

    def test_embed_with_account_adds_account_to_redirect(self):
        """Embed mode with *current_account_id* carries ``&account=<id>``."""
        record = _make_record()
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._constants.init_db", return_value=fake_conn
            ),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(self._PATCH_TRIG, return_value=decision),
        ):
            result = _build_detail_html(
                ":memory:",
                record.message_id,
                embed=True,
                current_account_id="acct-42",
            )

        assert result is not None
        assert 'name="redirect_to"' in result
        assert "&account=acct-42" in result
        # The redirect URL starts with /email/...?embed=1 and ends with the
        # account suffix.
        assert '"/email/' in result

    def test_embed_without_account_omits_account_from_redirect(self):
        """Embed mode without *current_account_id* omits ``&account=``."""
        record = _make_record()
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._constants.init_db", return_value=fake_conn
            ),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(self._PATCH_TRIG, return_value=decision),
        ):
            result = _build_detail_html(":memory:", record.message_id, embed=True)

        assert result is not None
        assert 'name="redirect_to"' in result
        assert "&account=" not in result

    def test_subject_script_injection_escaped_in_title(self):
        """A subject containing ``</title><script>`` must be escaped in <title>."""
        record = _make_record(subject="</title><script>alert(1)</script>")
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._constants.init_db", return_value=fake_conn
            ),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(self._PATCH_TRIG, return_value=decision),
        ):
            result = _build_detail_html(":memory:", record.message_id)

        assert result is not None
        # The raw script tag must NOT appear verbatim in the output.
        assert "</title><script>alert(1)</script>" not in result
        # The escaped form must appear in the <title> element.
        assert (
            "<title>Mail: &lt;/title&gt;&lt;script&gt;alert(1)&lt;/script&gt;</title>"
            in result
        )

    def test_aggregate_sentinel_omits_account(self):
        """``current_account_id="__all__"`` is treated as no-account."""
        record = _make_record()
        decision = TriageDecision(
            message_id=record.message_id, action="INBOX", source="agent"
        )
        fake_conn = mock.Mock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._constants.init_db", return_value=fake_conn
            ),
            mock.patch(
                "robotsix_auto_mail.db.get_record_by_message_id",
                return_value=record,
            ),
            mock.patch(self._PATCH_TRIG, return_value=decision),
        ):
            result = _build_detail_html(
                ":memory:",
                record.message_id,
                embed=True,
                current_account_id="__all__",
            )

        assert result is not None
        assert 'action="/move"' in result
        assert "?account=" not in result
        assert "&account=" not in result
