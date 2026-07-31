"""Unit tests for the standalone ``_build_add_account_form_html()`` function."""

from __future__ import annotations

from robotsix_auto_mail.server._account_mixin import _build_add_account_form_html


class TestBuildAddAccountFormHtml:
    """Tests for the standalone ``_build_add_account_form_html()`` function."""

    def test_contains_required_form_fields(self) -> None:
        html_out = _build_add_account_form_html()
        assert 'name="account_id"' in html_out
        assert 'name="imap_host"' in html_out
        assert 'name="smtp_host"' in html_out
        assert 'name="username"' in html_out
        assert 'name="password"' in html_out

    def test_contains_advanced_settings(self) -> None:
        html_out = _build_add_account_form_html()
        assert 'name="imap_port"' in html_out
        assert 'name="smtp_port"' in html_out
        assert 'name="imap_tls_mode"' in html_out
        assert 'name="smtp_tls_mode"' in html_out
        assert 'name="imap_folder"' in html_out

    def test_no_error_banner_when_error_empty(self) -> None:
        html_out = _build_add_account_form_html(error="")
        # The CSS contains ".error-banner" as a selector; check the div.
        assert '<div class="error-banner">' not in html_out

    def test_no_error_banner_when_error_default(self) -> None:
        html_out = _build_add_account_form_html()
        assert '<div class="error-banner">' not in html_out

    def test_error_banner_when_error_non_empty(self) -> None:
        html_out = _build_add_account_form_html(error="Oops!")
        assert "error-banner" in html_out
        assert "Oops!" in html_out

    def test_prefilled_values_appear(self) -> None:
        html_out = _build_add_account_form_html(
            prefill={"account_id": "my-id", "username": "me@host.com"},
        )
        assert 'value="my-id"' in html_out
        assert 'value="me@host.com"' in html_out

    def test_html_escapes_prefilled_values(self) -> None:
        html_out = _build_add_account_form_html(
            prefill={"account_id": '<script>alert("xss")</script>'},
        )
        assert '<script>alert("xss")</script>' not in html_out
        # The escaped version should contain &lt; and &gt;
        assert "&lt;script&gt;" in html_out

    def test_html_escapes_error_message(self) -> None:
        html_out = _build_add_account_form_html(error="<b>bold</b>")
        assert "<b>bold</b>" not in html_out
        assert "&lt;b&gt;bold&lt;/b&gt;" in html_out

    def test_prefilled_tls_mode_selected(self) -> None:
        html_out = _build_add_account_form_html(
            prefill={"imap_tls_mode": "starttls", "smtp_tls_mode": "none"},
        )
        assert '<option value="starttls" selected>starttls</option>' in html_out
        assert '<option value="none" selected>none</option>' in html_out

    def test_default_tls_mode_selected_when_no_prefill(self) -> None:
        html_out = _build_add_account_form_html()
        # IMAP default = direct-tls
        assert '<option value="direct-tls" selected>direct-tls</option>' in html_out
        # SMTP default = starttls
        assert '<option value="starttls" selected>starttls</option>' in html_out

    def test_prefilled_imap_folder(self) -> None:
        html_out = _build_add_account_form_html(
            prefill={"imap_folder": "[Gmail]/All Mail"},
        )
        assert 'value="[Gmail]/All Mail"' in html_out

    def test_form_action_and_method(self) -> None:
        html_out = _build_add_account_form_html()
        assert 'method="post"' in html_out
        assert 'action="/add-account"' in html_out

    def test_cancel_link(self) -> None:
        html_out = _build_add_account_form_html()
        assert 'href="/board"' in html_out
        assert "Cancel" in html_out

    def test_submit_button(self) -> None:
        html_out = _build_add_account_form_html()
        assert "Add Account" in html_out
        assert 'type="submit"' in html_out

    def test_password_not_prefilled(self) -> None:
        """Password field should never be pre-filled for security."""
        html_out = _build_add_account_form_html(
            prefill={"password": "secret123"},
        )
        # Password field should NOT contain the value
        assert 'value="secret123"' not in html_out
