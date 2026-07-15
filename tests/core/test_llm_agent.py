"""Unit tests for ``_run_llm_agent`` in ``src/robotsix_auto_mail/_llm_agent.py``.

Exercises the resolve → build → run orchestration and its error-wrapping
paths — the function's core value-add over each caller duplicating the
boilerplate manually.
"""

from __future__ import annotations

from unittest import mock

import pydantic
import pytest

from robotsix_auto_mail.core._llm_agent import _run_llm_agent
from robotsix_auto_mail.config import ConfigurationError

# ---------------------------------------------------------------------------
# Test output model
# ---------------------------------------------------------------------------


class _FakeOutput(pydantic.BaseModel):
    """Plain pydantic model used as ``output_model`` in tests."""

    value: str = "ok"


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_deps():
    """Mock all three integration points of ``_run_llm_agent``.

    Yields ``(mock_resolve_key, mock_get_provider, mock_run_agent)`` so
    individual tests can adjust side effects / return values.
    """
    with (
        mock.patch("robotsix_auto_mail.core._llm_agent.resolve_llm_api_key") as mock_key,
        mock.patch("robotsix_llmio.core.get_provider_for_identifier") as mock_get_prov,
        mock.patch("robotsix_llmio.core.run_agent") as mock_run,
    ):
        mock_key.return_value = "sk-test-key"
        mock_provider = mock.Mock()
        mock_agent = mock.Mock()
        mock_provider.build_agent.return_value = mock_agent
        mock_get_prov.return_value = mock_provider
        mock_run.return_value = mock.Mock(output=_FakeOutput(value="ok"))
        yield mock_key, mock_get_prov, mock_run


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_returns_typed_output(mock_deps):
    """Full orchestration succeeds and returns the validated output model."""
    mock_key, mock_get_prov, mock_run = mock_deps

    result = _run_llm_agent(
        api_key=None,
        provider_model="openrouter/deepseek",
        level=1,
        system_prompt="You are a helpful assistant.",
        output_model=_FakeOutput,
        user_message="hello",
        label="test-run",
        what="unit test",
        exc_type=RuntimeError,
    )

    assert isinstance(result, _FakeOutput)
    assert result.value == "ok"
    mock_key.assert_called_once_with(None)
    mock_get_prov.assert_called_once()
    mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Missing API key
# ---------------------------------------------------------------------------


def test_missing_api_key_raises_exc_type(mock_deps):
    """``resolve_llm_api_key`` → ``ConfigurationError`` is caught and
    re-raised as the caller's ``exc_type``."""
    mock_key, mock_get_prov, mock_run = mock_deps
    mock_key.side_effect = ConfigurationError("no API key configured")

    with pytest.raises(RuntimeError, match="no API key configured"):
        _run_llm_agent(
            api_key=None,
            provider_model=None,
            level=1,
            system_prompt="test",
            output_model=_FakeOutput,
            user_message="hello",
            label="test",
            what="testing",
            exc_type=RuntimeError,
        )

    # Provider resolution and agent run must not be reached.
    mock_get_prov.assert_not_called()
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# run_agent failure
# ---------------------------------------------------------------------------


def test_run_agent_failure_raises_exc_type(mock_deps):
    """When ``run_agent`` raises an arbitrary ``Exception``, it is caught
    and re-raised as the caller's ``exc_type``."""
    mock_key, mock_get_prov, mock_run = mock_deps
    mock_run.side_effect = ValueError("LLM timeout")

    with pytest.raises(RuntimeError, match="LLM timeout"):
        _run_llm_agent(
            api_key="sk-test",
            provider_model=None,
            level=1,
            system_prompt="test",
            output_model=_FakeOutput,
            user_message="hello",
            label="test",
            what="testing",
            exc_type=RuntimeError,
        )

    mock_key.assert_called_once()
    mock_get_prov.assert_called_once()
    mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Level mapping
# ---------------------------------------------------------------------------


def test_level2_completes_successfully(mock_deps):
    """``level=2`` (non-cheap) selects level=2 and the call succeeds."""
    _mock_key, mock_get_prov, mock_run = mock_deps

    result = _run_llm_agent(
        api_key=None,
        provider_model=None,
        level=2,
        system_prompt="test",
        output_model=_FakeOutput,
        user_message="hello",
        label="test",
        what="testing",
        exc_type=RuntimeError,
    )

    assert isinstance(result, _FakeOutput)
    assert result.value == "ok"
    mock_get_prov.assert_called_once()
    mock_run.assert_called_once()
