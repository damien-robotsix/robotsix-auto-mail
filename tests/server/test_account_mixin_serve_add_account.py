"""Unit tests for ``_serve_add_account()`` — GET form rendering and POST
re-render with error / prefill.
"""

from __future__ import annotations

from tests.server._test_helpers import _AccountMixinFakeHandler


class TestServeAddAccount:
    """Tests for ``_serve_add_account()`` — GET form rendering and POST
    re-render with error / prefill."""

    def test_renders_form_without_error(self) -> None:
        handler = _AccountMixinFakeHandler()
        handler._serve_add_account()
        handler._send_response.assert_called_once()
        body, kwargs = handler._send_response.call_args
        assert "Add Mail Account" in body[0]
        # The CSS contains ".error-banner" as a selector; check for the
        # actual error-banner div, not just the CSS class name.
        assert '<div class="error-banner">' not in body[0]
        assert kwargs.get("content_type") == "text/html; charset=utf-8"

    def test_renders_form_with_error_banner(self) -> None:
        handler = _AccountMixinFakeHandler()
        handler._serve_add_account(error="Something went wrong")
        handler._send_response.assert_called_once()
        body, _kwargs = handler._send_response.call_args
        assert "error-banner" in body[0]
        assert "Something went wrong" in body[0]

    def test_renders_form_with_prefilled_values(self) -> None:
        handler = _AccountMixinFakeHandler()
        handler._serve_add_account(
            prefill={"account_id": "my-account", "username": "me@host.com"},
        )
        handler._send_response.assert_called_once()
        body, _kwargs = handler._send_response.call_args
        assert 'value="my-account"' in body[0]
        assert 'value="me@host.com"' in body[0]

    def test_renders_form_with_error_and_prefill(self) -> None:
        handler = _AccountMixinFakeHandler()
        handler._serve_add_account(
            error="Bad input",
            prefill={"imap_host": "bad-host"},
        )
        handler._send_response.assert_called_once()
        body, _kwargs = handler._send_response.call_args
        assert "error-banner" in body[0]
        assert "Bad input" in body[0]
        assert 'value="bad-host"' in body[0]
