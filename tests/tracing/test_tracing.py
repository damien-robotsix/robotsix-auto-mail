"""Tests for the Langfuse tracing initialisation module."""

from __future__ import annotations

from unittest import mock

from robotsix_auto_mail.tracing import init_langfuse_tracing


def test_init_no_credentials() -> None:
    """Returns False when no Langfuse env vars are set (no-op)."""
    with mock.patch(
        "robotsix_auto_mail.tracing.setup_langfuse_tracing",
        return_value=False,
    ) as mock_setup:
        result = init_langfuse_tracing()
        assert result is False
        mock_setup.assert_called_once_with(service_name="robotsix-auto-mail")


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
        mock_setup.assert_called_once_with(service_name="robotsix-auto-mail")
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
