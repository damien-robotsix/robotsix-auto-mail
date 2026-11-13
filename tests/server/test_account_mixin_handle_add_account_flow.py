"""Flow tests for ``AccountService.handle_add_account()`` through the helpers
extracted when the handler was split: the "Detect Settings" branch, the
normal submit path (including the embedded ``origin=settings`` redirect),
and form-parsing / validation edge cases."""

from __future__ import annotations

from typing import Any
from unittest import mock
from urllib.parse import urlencode

import pytest

from robotsix_auto_mail.detect import MailProvider
from robotsix_auto_mail.server import _account_service
from robotsix_auto_mail.server._account_service import (
    AccountService,
    _extract_account_form_fields,
    _lookup_provider_for_email,
    _validate_account_form,
)
from tests.server._test_helpers import _AccountServiceContext

_MOD = "robotsix_auto_mail.server._account_service"

_VALID_FIELDS = {
    "account_id": "test",
    "imap_host": "imap.example.com",
    "smtp_host": "smtp.example.com",
    "username": "user@example.com",
    "password": "secret",
}


def _post(body: dict[str, str] | str) -> _AccountServiceContext:
    """Return a stub context carrying *body* as a URL-encoded POST."""
    raw = body if isinstance(body, str) else urlencode(body)
    ctx = _AccountServiceContext()
    ctx.headers.get.return_value = str(len(raw.encode("utf-8")))
    ctx.rfile.read.return_value = raw.encode("utf-8")
    return ctx


def _handle(ctx: _AccountServiceContext) -> None:
    """Drive ``AccountService.handle_add_account`` with the stub *ctx*."""
    AccountService(db_path=ctx.db_path).handle_add_account(ctx)


def _rendered(ctx: _AccountServiceContext) -> str:
    """Return the single HTML body sent through ``_send_response``."""
    ctx._send_response.assert_called_once()
    body: str = ctx._send_response.call_args[0][0]
    return body


# ---------------------------------------------------------------------------
# "Detect Settings" branch
# ---------------------------------------------------------------------------


class TestDetectAction:
    """``action=detect`` runs provider detection and re-renders the form."""

    def test_detect_without_username_renders_error(self) -> None:
        ctx = _post({"action": "detect", "account_id": "a"})
        with mock.patch(f"{_MOD}._lookup_provider_for_email") as lookup:
            _handle(ctx)
        lookup.assert_not_called()
        ctx._redirect.assert_not_called()
        body = _rendered(ctx)
        assert "Enter an email address in the Username field" in body
        # Detection is checked before required-field validation.
        assert "Missing required fields" not in body

    def test_detect_whitespace_username_treated_as_empty(self) -> None:
        ctx = _post({"action": "detect", "username": "   "})
        with mock.patch(f"{_MOD}._lookup_provider_for_email") as lookup:
            _handle(ctx)
        lookup.assert_not_called()
        assert "Enter an email address" in _rendered(ctx)

    def test_detect_success_prefills_detected_settings(self) -> None:
        provider = MailProvider(
            imap_host="imap.detected.example",
            smtp_host="smtp.detected.example",
            imap_port=1993,
            smtp_port=1465,
            imap_tls_mode="starttls",
            smtp_tls_mode="none",
        )
        ctx = _post(
            {
                "action": "detect",
                "account_id": "work",
                "label": "Work",
                "username": " me@detected.example ",
                "password": "hunter2",
                "imap_host": "old-imap",
            }
        )
        with (
            mock.patch(
                f"{_MOD}._lookup_provider_for_email", return_value=(provider, "")
            ) as lookup,
            mock.patch(f"{_MOD}.save_accounts") as save,
            mock.patch(f"{_MOD}.load_accounts") as load,
        ):
            _handle(ctx)
        lookup.assert_called_once_with("me@detected.example")
        # Detection never persists anything.
        save.assert_not_called()
        load.assert_not_called()
        ctx._redirect.assert_not_called()
        body = _rendered(ctx)
        assert 'class="success-banner"' in body
        assert "Settings detected for me@detected.example" in body
        assert "IMAP imap.detected.example:1993" in body
        assert "SMTP smtp.detected.example:1465" in body
        # Detected values override the submitted ones.
        assert 'value="imap.detected.example"' in body
        assert 'value="old-imap"' not in body
        assert 'value="smtp.detected.example"' in body
        assert 'value="1993"' in body
        assert 'value="1465"' in body
        assert '<option value="starttls" selected>' in body
        assert '<option value="none" selected>' in body
        # Other submitted values are echoed back; the password never is.
        assert 'value="work"' in body
        assert 'value="Work"' in body
        assert "hunter2" not in body

    def test_detect_failure_renders_error_with_reason(self) -> None:
        ctx = _post({"action": "detect", "username": "me@unknown.example"})
        with mock.patch(
            f"{_MOD}._lookup_provider_for_email",
            return_value=(None, "Domain lookup failed."),
        ):
            _handle(ctx)
        ctx._redirect.assert_not_called()
        body = _rendered(ctx)
        assert 'class="error-banner"' in body
        assert "Could not auto-detect settings for me@unknown.example" in body
        assert "Domain lookup failed." in body
        assert "enter IMAP/SMTP settings manually" in body
        assert 'value="me@unknown.example"' in body

    def test_detect_failure_without_reason_uses_fallback(self) -> None:
        ctx = _post({"action": "detect", "username": "me@unknown.example"})
        with mock.patch(f"{_MOD}._lookup_provider_for_email", return_value=(None, "")):
            _handle(ctx)
        assert "No provider match found." in _rendered(ctx)

    def test_detect_escapes_username_in_banner(self) -> None:
        ctx = _post({"action": "detect", "username": "<b>x</b>@example.com"})
        with mock.patch(f"{_MOD}._lookup_provider_for_email", return_value=(None, "")):
            _handle(ctx)
        body = _rendered(ctx)
        assert "<b>x</b>" not in body

    def test_detect_preserves_settings_origin(self) -> None:
        ctx = _post(
            {"action": "detect", "username": "me@x.example", "origin": "settings"}
        )
        with mock.patch(f"{_MOD}._lookup_provider_for_email", return_value=(None, "")):
            _handle(ctx)
        body = _rendered(ctx)
        assert '<input type="hidden" name="origin" value="settings">' in body
        assert 'href="/settings-panel"' in body

    def test_action_value_is_stripped(self) -> None:
        ctx = _post({"action": " detect ", "username": ""})
        _handle(ctx)
        assert "Enter an email address" in _rendered(ctx)


class TestLookupProviderForEmail:
    """``_lookup_provider_for_email`` tries autoconfig, then MX."""

    def test_autoconfig_hit_short_circuits(self) -> None:
        provider = MailProvider(imap_host="i", smtp_host="s")
        with (
            mock.patch(f"{_MOD}.autoconfig_lookup", return_value=provider) as ac,
            mock.patch(f"{_MOD}.mx_lookup") as mx,
        ):
            assert _lookup_provider_for_email("a@b.example") == (provider, "")
        ac.assert_called_once_with("a@b.example", timeout=4.0)
        mx.assert_not_called()

    def test_mx_fallback(self) -> None:
        provider = MailProvider(imap_host="i", smtp_host="s")
        with (
            mock.patch(f"{_MOD}.autoconfig_lookup", return_value=None),
            mock.patch(f"{_MOD}.mx_lookup", return_value=["mx.b.example"]) as mx,
            mock.patch(f"{_MOD}.provider_from_mx", return_value=provider) as pfm,
        ):
            assert _lookup_provider_for_email("a@b.example") == (provider, "")
        mx.assert_called_once_with("a@b.example", timeout=4.0)
        pfm.assert_called_once_with(["mx.b.example"])

    def test_no_match_returns_error(self) -> None:
        with (
            mock.patch(f"{_MOD}.autoconfig_lookup", return_value=None),
            mock.patch(f"{_MOD}.mx_lookup", return_value=[]),
            mock.patch(f"{_MOD}.provider_from_mx", return_value=None),
        ):
            provider, error = _lookup_provider_for_email("a@b.example")
        assert provider is None
        assert "not in the known-provider database" in error


# ---------------------------------------------------------------------------
# Normal submit path
# ---------------------------------------------------------------------------


class TestSubmitPath:
    """Valid submissions persist, seed the settings store and redirect."""

    def _run(
        self, ctx: _AccountServiceContext
    ) -> tuple[mock.MagicMock, mock.MagicMock]:
        ctx.server.RequestHandlerClass.keywords = {"accounts": None}
        with (
            mock.patch(f"{_MOD}.load_accounts", side_effect=FileNotFoundError),
            mock.patch(f"{_MOD}.save_accounts") as save,
            mock.patch(f"{_MOD}._seed_account_settings_store") as seed,
        ):
            _handle(ctx)
        return save, seed

    @pytest.mark.parametrize("action", ["", "add"])
    def test_submit_redirects_to_board(self, action: str) -> None:
        body = dict(_VALID_FIELDS)
        if action:
            body["action"] = action
        ctx = _post(body)
        save, seed = self._run(ctx)
        ctx._redirect.assert_called_once_with("/board", code=303)
        ctx._send_response.assert_not_called()
        save.assert_called_once()
        seed.assert_called_once()
        saved: Any = save.call_args[0][0]
        (account,) = saved.accounts
        assert seed.call_args[0][0] is account
        assert account.account_id == "test"
        assert account.label is None
        cfg = account.config
        assert cfg.imap_host == "imap.example.com"
        assert cfg.smtp_host == "smtp.example.com"
        assert cfg.username == "user@example.com"
        assert cfg.password.get_secret_value() == "secret"
        assert cfg.imap_port == 993
        assert cfg.smtp_port == 587
        assert cfg.imap_folder == "INBOX"
        assert cfg.db_path == ".data/test/mail.db"

    def test_submit_strips_and_applies_optional_fields(self) -> None:
        ctx = _post(
            {
                **{k: f"  {v}  " for k, v in _VALID_FIELDS.items()},
                "label": " Work ",
                "imap_port": " 1143 ",
                "smtp_port": "25",
                "imap_tls_mode": "starttls",
                "smtp_tls_mode": "none",
                "imap_folder": "Archive",
            }
        )
        save, _seed = self._run(ctx)
        (account,) = save.call_args[0][0].accounts
        assert account.account_id == "test"
        assert account.label == "Work"
        cfg = account.config
        assert cfg.imap_host == "imap.example.com"
        assert cfg.imap_port == 1143
        assert cfg.smtp_port == 25
        assert cfg.imap_tls_mode == "starttls"
        assert cfg.smtp_tls_mode == "none"
        assert cfg.imap_folder == "Archive"

    def test_settings_origin_redirects_parent_frame(self) -> None:
        ctx = _post({**_VALID_FIELDS, "account_id": "my.acct", "origin": "settings"})
        save, _seed = self._run(ctx)
        save.assert_called_once()
        ctx._redirect.assert_not_called()
        body = _rendered(ctx)
        assert ctx._send_response.call_args.kwargs["status"] == 200
        assert "data-redirect='/settings-panel?added=my.acct'" in body
        assert '<script src="/static/add-account-redirect.js"></script>' in body

    def test_seed_failure_does_not_block_redirect(self) -> None:
        ctx = _post(_VALID_FIELDS)
        ctx.server.RequestHandlerClass.keywords = {"accounts": None}
        with (
            mock.patch(f"{_MOD}.load_accounts", side_effect=FileNotFoundError),
            mock.patch(f"{_MOD}.save_accounts"),
            mock.patch("robotsix_auto_mail.db.init_db", side_effect=OSError("no disk")),
        ):
            _handle(ctx)
        ctx._redirect.assert_called_once_with("/board", code=303)

    def test_validation_error_in_settings_origin_keeps_embed(self) -> None:
        ctx = _post({"account_id": "x", "origin": "settings"})
        with mock.patch(f"{_MOD}.save_accounts") as save:
            _handle(ctx)
        save.assert_not_called()
        body = _rendered(ctx)
        assert "Missing required fields" in body
        assert '<input type="hidden" name="origin" value="settings">' in body


# ---------------------------------------------------------------------------
# Form parsing and validation edge cases
# ---------------------------------------------------------------------------


class TestFormEdgeCases:
    """Missing, blank and malformed fields are rejected before persisting."""

    def test_empty_body_lists_every_required_field(self) -> None:
        ctx = _post("")
        ctx.headers.get.return_value = "0"
        with mock.patch(f"{_MOD}.save_accounts") as save:
            _handle(ctx)
        save.assert_not_called()
        body = _rendered(ctx)
        assert (
            "Missing required fields: account_id, imap_host, smtp_host,"
            " username, password"
        ) in body

    def test_whitespace_only_required_field_counts_as_missing(self) -> None:
        ctx = _post({**_VALID_FIELDS, "password": "   "})
        with mock.patch(f"{_MOD}.save_accounts") as save:
            _handle(ctx)
        save.assert_not_called()
        assert "Missing required fields: password" in _rendered(ctx)

    def test_error_rerender_echoes_values_but_not_password(self) -> None:
        ctx = _post({**_VALID_FIELDS, "password": "hunter2", "imap_port": "nope"})
        _handle(ctx)
        body = _rendered(ctx)
        assert "IMAP Port must be a number." in body
        assert 'value="imap.example.com"' in body
        assert 'value="user@example.com"' in body
        assert "hunter2" not in body

    def test_malformed_values_are_html_escaped(self) -> None:
        ctx = _post({**_VALID_FIELDS, "account_id": '"><script>x</script>'})
        _handle(ctx)
        body = _rendered(ctx)
        assert "invalid characters" in body
        assert "<script>x</script>" not in body

    def test_non_utf8_body_is_decoded_with_replacement(self) -> None:
        ctx = _post("")
        raw = b"account_id=\xff\xfe"
        ctx.headers.get.return_value = str(len(raw))
        ctx.rfile.read.return_value = raw
        _handle(ctx)
        body = _rendered(ctx)
        # Undecodable bytes do not crash the handler; the required-field
        # check runs before the account_id charset check.
        assert "invalid characters" not in body
        assert "Missing required fields" in body

    def test_repeated_keys_use_first_value(self) -> None:
        fields, prefill = _extract_account_form_fields(
            {"account_id": ["first", "second"], "password": ["pw"]}
        )
        assert fields["account_id"] == "first"
        assert fields["password"] == "pw"
        assert "password" not in prefill
        # Every known field is present, absent ones as empty strings.
        assert set(fields) == set(_account_service._FORM_FIELDS)
        assert fields["label"] == ""

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            ({"account_id": "bad/id"}, "invalid characters"),
            ({"imap_tls_mode": "TLS"}, "Invalid IMAP TLS mode: TLS"),
            ({"smtp_tls_mode": "ssl3"}, "Invalid SMTP TLS mode: ssl3"),
            ({"imap_port": "99.5"}, "IMAP Port must be a number."),
            ({"smtp_port": "25a"}, "SMTP Port must be a number."),
        ],
    )
    def test_validate_account_form_rejects(
        self, overrides: dict[str, str], expected: str
    ) -> None:
        account, error = _validate_account_form({**_VALID_FIELDS, **overrides})
        assert account is None
        assert expected in error

    def test_validate_account_form_reports_first_failure_only(self) -> None:
        account, error = _validate_account_form(
            {**_VALID_FIELDS, "imap_tls_mode": "bad", "imap_port": "bad"}
        )
        assert account is None
        assert "TLS mode" in error
        assert "Port" not in error
