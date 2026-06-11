"""Tests for the Langfuse tracing initialisation module."""

from __future__ import annotations

from unittest import mock

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.tracing import init_langfuse_tracing


def _make_config(
    *,
    langfuse_public_key: str = "",
    langfuse_secret_key: str = "",
    langfuse_base_url: str = "",
) -> MailConfig:
    """Build a minimal ``MailConfig`` for tracing tests."""
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="secret",
        langfuse_public_key=langfuse_public_key,
        langfuse_secret_key=langfuse_secret_key,
        langfuse_base_url=langfuse_base_url,
    )


def test_init_no_credentials() -> None:
    """Returns False when no Langfuse env vars are set (no-op)."""
    with mock.patch(
        "robotsix_auto_mail.tracing.setup_langfuse_tracing",
        return_value=False,
    ) as mock_setup:
        result = init_langfuse_tracing()
        assert result is False
        mock_setup.assert_called_once_with(
            service_name="robotsix-auto-mail",
            public_key=None,
            secret_key=None,
            base_url=None,
        )


def test_init_with_credentials() -> None:
    """Returns True and installs signal handlers when setup succeeds."""
    with (
        mock.patch(
            "robotsix_auto_mail.tracing.setup_langfuse_tracing",
            return_value=True,
        ) as mock_setup,
        mock.patch(
            "robotsix_auto_mail.tracing.install_signal_handlers"
        ) as mock_install,
    ):
        result = init_langfuse_tracing()
        assert result is True
        mock_setup.assert_called_once_with(
            service_name="robotsix-auto-mail",
            public_key=None,
            secret_key=None,
            base_url=None,
        )
        mock_install.assert_called_once()


def test_init_setup_fails_no_handlers() -> None:
    """Does not install signal handlers when setup returns False."""
    with (
        mock.patch(
            "robotsix_auto_mail.tracing.setup_langfuse_tracing",
            return_value=False,
        ),
        mock.patch(
            "robotsix_auto_mail.tracing.install_signal_handlers"
        ) as mock_install,
    ):
        init_langfuse_tracing()
        mock_install.assert_not_called()


def test_init_passes_config_credentials() -> None:
    """Credentials from a MailConfig are forwarded to setup_langfuse_tracing."""
    config = _make_config(
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key="sk-lf-test",
        langfuse_base_url="https://langfuse.example.net",
    )
    with mock.patch(
        "robotsix_auto_mail.tracing.setup_langfuse_tracing",
        return_value=True,
    ) as mock_setup:
        init_langfuse_tracing(config)
        mock_setup.assert_called_once_with(
            service_name="robotsix-auto-mail",
            public_key="pk-lf-test",
            secret_key="sk-lf-test",
            base_url="https://langfuse.example.net",
        )


def test_init_empty_config_fields_become_none() -> None:
    """Empty-string Langfuse fields convert to None (env-fallback no-op)."""
    config = _make_config()  # langfuse_* default to ""
    with mock.patch(
        "robotsix_auto_mail.tracing.setup_langfuse_tracing",
        return_value=False,
    ) as mock_setup:
        init_langfuse_tracing(config)
        mock_setup.assert_called_once_with(
            service_name="robotsix-auto-mail",
            public_key=None,
            secret_key=None,
            base_url=None,
        )
