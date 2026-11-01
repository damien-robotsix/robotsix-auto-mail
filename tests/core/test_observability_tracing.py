"""Tests for the Langfuse tracing initialisation module."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.core._observability import init_langfuse_tracing
from tests.conftest import _make_langfuse_config

_CONFIGURED = dict(public_key="pk-lf-test", secret_key="sk-lf-test")


def test_init_no_credentials_never_calls_llmio(monkeypatch: pytest.MonkeyPatch) -> None:
    """No canonical block → tracing disabled; llmio's env fallback is never
    reached even when deploy-plane LANGFUSE_* env vars are set."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-env")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-env")
    with mock.patch(
        "robotsix_auto_mail.core._observability.setup_langfuse_tracing",
    ) as mock_setup:
        assert init_langfuse_tracing() is False
        mock_setup.assert_not_called()


def test_init_with_credentials() -> None:
    """Returns True and installs signal handlers when setup succeeds."""
    with (
        mock.patch(
            "robotsix_auto_mail.core._observability.setup_langfuse_tracing",
            return_value=True,
        ),
        mock.patch(
            "robotsix_auto_mail.core._observability.install_signal_handlers"
        ) as mock_install,
    ):
        result = init_langfuse_tracing(_make_langfuse_config(**_CONFIGURED))
        assert result is True
        mock_install.assert_called_once()


def test_init_setup_fails_no_handlers() -> None:
    """Does not install signal handlers when setup returns False."""
    with (
        mock.patch(
            "robotsix_auto_mail.core._observability.setup_langfuse_tracing",
            return_value=False,
        ),
        mock.patch(
            "robotsix_auto_mail.core._observability.install_signal_handlers"
        ) as mock_install,
    ):
        init_langfuse_tracing(_make_langfuse_config(**_CONFIGURED))
        mock_install.assert_not_called()


def test_init_empty_host_uses_cloud_not_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset host resolves to Langfuse Cloud, not LANGFUSE_BASE_URL."""
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://env.example.net")
    with mock.patch(
        "robotsix_auto_mail.core._observability.setup_langfuse_tracing",
        return_value=True,
    ) as mock_setup:
        init_langfuse_tracing(_make_langfuse_config(**_CONFIGURED))
        assert mock_setup.call_args.kwargs["base_url"] == "https://cloud.langfuse.com"


def test_init_passes_config_credentials() -> None:
    """Credentials from the canonical block reach setup_langfuse_tracing."""
    config = _make_langfuse_config(
        public_key="pk-lf-test",
        secret_key="sk-lf-test",
        base_url="https://langfuse.example.net",
    )
    with mock.patch(
        "robotsix_auto_mail.core._observability.setup_langfuse_tracing",
        return_value=True,
    ) as mock_setup:
        init_langfuse_tracing(config)
        mock_setup.assert_called_once_with(
            service_name="robotsix-auto-mail",
            public_key="pk-lf-test",
            secret_key="sk-lf-test",
            base_url="https://langfuse.example.net",
        )


def test_init_unset_project_disables_tracing() -> None:
    """A declared-but-unset project disables tracing (no env fallback)."""
    config = _make_langfuse_config()  # the project is declared but unset
    with mock.patch(
        "robotsix_auto_mail.core._observability.setup_langfuse_tracing",
    ) as mock_setup:
        assert init_langfuse_tracing(config) is False
        mock_setup.assert_not_called()


def test_init_half_filled_project_disables_tracing() -> None:
    """One key on its own is unconfigured, not a partial credential."""
    config = _make_langfuse_config(public_key="pk-only")
    with mock.patch(
        "robotsix_auto_mail.core._observability.setup_langfuse_tracing",
    ) as mock_setup:
        assert init_langfuse_tracing(config) is False
        mock_setup.assert_not_called()


def test_init_ignores_a_project_under_another_alias() -> None:
    """Only this component's own function is traced.

    Picking up any project in the block would send auto-mail's traces into a
    neighbouring function's project and break the one-project-per-function
    split the standard requires.
    """
    config = _make_langfuse_config(
        public_key="pk-other",
        secret_key="sk-other",
        alias="some-other-function",
    )
    with mock.patch(
        "robotsix_auto_mail.core._observability.setup_langfuse_tracing",
    ) as mock_setup:
        assert init_langfuse_tracing(config) is False
        mock_setup.assert_not_called()
