"""Unit tests for ``_BoardAuthMixin`` methods.

Drives the mixin directly against a mock handler *self*, isolating the
logic from the HTTP transport and covering the post-auth auto-probe
behaviour added after the device-code flow completes.
"""

from __future__ import annotations

from typing import Any, Callable
from unittest import mock

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.server._auth_mixin import _BoardAuthMixin

# ---------------------------------------------------------------------------
# Fake handler factory
# ---------------------------------------------------------------------------


class _FakeHandler(_BoardAuthMixin):
    """Concrete handler that wires the ``BoardHandlerProtocol`` attributes
    to MagicMock defaults so mixin methods can be called directly."""

    def __init__(
        self,
        db_path: str,
        mail_config: MailConfig | None = None,
    ) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self.accounts = None
        self._current_account_id = None
        self._aggregate = False
        self._account_cookie = None
        self.default_account_id = None
        self.headers = mock.MagicMock()
        self.rfile = mock.MagicMock()
        self._send_response = mock.MagicMock()
        self._redirect = mock.MagicMock()
        self._not_found = mock.MagicMock()
        self._bad_request = mock.MagicMock()
        self._serve_json = mock.MagicMock()


# ---------------------------------------------------------------------------
# Synchronous fake ``Thread`` so background daemon code is deterministic.
# ---------------------------------------------------------------------------


class _SyncThread:
    """Drop-in replacement for ``threading.Thread`` that runs *target*
    synchronously inside ``start()``."""

    def __init__(
        self,
        group: object = None,
        target: Callable[..., None] | None = None,
        name: str | None = None,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        *,
        daemon: bool | None = None,
    ) -> None:
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self) -> None:
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_microsoft_config(tmp_db_path: str) -> MailConfig:
    """Return a minimal ``MailConfig`` wired for Microsoft OAuth2."""
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="pass",
        oauth2_provider="microsoft",
        oauth2_client_id="test-client-id",
        oauth2_tenant="common",
        db_path=tmp_db_path,
    )


def _patch_device_code_login(calls_on_prompt: bool = True) -> Callable[..., None]:
    """Return a mock ``device_code_login`` side-effect.

    When *calls_on_prompt* is True, the side-effect invokes the
    ``on_prompt`` callback with a minimal flow dict before returning.
    """

    def _side_effect(
        config: MailConfig,
        *,
        on_prompt: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if on_prompt is not None:
            on_prompt(
                {
                    "message": "Go to https://microsoft.com/devicelogin",
                    "user_code": "ABC123",
                    "verification_uri": "https://microsoft.com/devicelogin",
                }
            )

    return _side_effect


# ===================================================================
# Tests
# ===================================================================


class TestPostAuthAutoProbe:
    """Verify that ``_run()`` probes account health and writes the DB
    row before flipping the flow status to ``"success"``."""

    def test_auth_success_writes_ok_health(self, tmp_db_path: str) -> None:
        """After device_code_login returns, probe_account must be called
        and write_account_health must receive status="ok"."""
        cfg = _make_microsoft_config(tmp_db_path)
        handler = _FakeHandler(tmp_db_path, mail_config=cfg)
        handler.headers.get.return_value = 0
        handler.rfile.read.return_value = b""

        mock_write = mock.MagicMock()
        mock_conn = mock.MagicMock()

        with (
            mock.patch(
                "robotsix_auto_mail.server._auth_mixin.device_code_login",
                side_effect=_patch_device_code_login(),
            ),
            mock.patch(
                "robotsix_auto_mail.server._constants.init_db",
                return_value=mock_conn,
            ),
            mock.patch(
                "robotsix_auto_mail.core.health.probe_account",
                return_value=("ok", None),
            ),
            mock.patch(
                "robotsix_auto_mail.db.queries.write_account_health",
                mock_write,
            ),
            mock.patch("threading.Thread", _SyncThread),
        ):
            handler._handle_auth_start()

        # Assert the probe wrote "ok" health.
        mock_write.assert_called_once()
        _call_args, call_kwargs = mock_write.call_args
        assert call_kwargs.get("status") == "ok"
        assert call_kwargs.get("error") is None

        # Assert the flow ended in "success".
        assert _BoardAuthMixin._AUTH_FLOWS["single"]["status"] == "success"

    def test_auth_success_probe_failure_still_succeeds(self, tmp_db_path: str) -> None:
        """When probe_account raises, the flow still finishes with
        status="success" — probe failure is non-fatal."""
        cfg = _make_microsoft_config(tmp_db_path)
        handler = _FakeHandler(tmp_db_path, mail_config=cfg)
        handler.headers.get.return_value = 0
        handler.rfile.read.return_value = b""

        with (
            mock.patch(
                "robotsix_auto_mail.server._auth_mixin.device_code_login",
                side_effect=_patch_device_code_login(),
            ),
            mock.patch(
                "robotsix_auto_mail.core.health.probe_account",
                side_effect=RuntimeError("IMAP timeout"),
            ),
            mock.patch(
                "robotsix_auto_mail.server._constants.init_db",
            ) as mock_init_db,
            mock.patch(
                "robotsix_auto_mail.db.queries.write_account_health",
            ) as mock_write,
            mock.patch("threading.Thread", _SyncThread),
        ):
            handler._handle_auth_start()

        # The probe failed, so neither init_db nor write_account_health
        # should have been called.
        mock_init_db.assert_not_called()
        mock_write.assert_not_called()

        # The flow must still succeed.
        assert _BoardAuthMixin._AUTH_FLOWS["single"]["status"] == "success"
