"""Unit tests for ``_run_llm_agent`` in ``src/robotsix_auto_mail/_llm_agent.py``.

Exercises the resolve → build → run orchestration and its error-wrapping
paths — the function's core value-add over each caller duplicating the
boilerplate manually.
"""

from __future__ import annotations

from unittest import mock

import pydantic
import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior

from robotsix_auto_mail.core._llm_agent import _run_llm_agent

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
        mock.patch(
            "robotsix_auto_mail.core._llm_agent.resolve_llm_api_key"
        ) as mock_key,
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
        provider_model="openrouter-deepseek/deepseek-v4-flash-latest",
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
    mock_key.assert_called_once_with(None, raise_on_missing=False)
    mock_get_prov.assert_called_once()
    mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Missing API key
# ---------------------------------------------------------------------------


def test_no_api_key_ok_when_provider_is_claude_sdk(mock_deps):
    """The default (Claude SDK) slot is keyless: a missing OpenRouter key
    must not block the call."""
    mock_key, mock_get_prov, _mock_run = mock_deps
    mock_key.return_value = ""  # no OpenRouter key configured anywhere

    result = _run_llm_agent(
        api_key=None,
        provider_model=None,  # llmio default slot: claudeSDK
        level=1,
        system_prompt="test",
        output_model=_FakeOutput,
        user_message="hello",
        label="test",
        what="testing",
        exc_type=RuntimeError,
    )

    assert isinstance(result, _FakeOutput)
    # No api_key kwarg reaches the keyless provider.
    assert "api_key" not in mock_get_prov.call_args.kwargs


def test_missing_api_key_raises_only_for_openrouter_model(mock_deps):
    """An OpenRouter-backed model without a configured key raises the
    caller's ``exc_type`` with a pointer to the config location."""
    mock_key, mock_get_prov, mock_run = mock_deps
    mock_key.return_value = ""

    with pytest.raises(RuntimeError, match="OpenRouter API key"):
        _run_llm_agent(
            api_key=None,
            provider_model="openrouter-deepseek/deepseek-v4-flash-latest",
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


# ---------------------------------------------------------------------------
# output_retries passthrough
# ---------------------------------------------------------------------------


def test_output_retries_forwarded_to_build_agent(mock_deps):
    """When ``output_retries`` is set, it is passed as ``retries`` to
    ``build_agent``."""
    _mock_key, mock_get_prov, _mock_run = mock_deps

    _run_llm_agent(
        api_key=None,
        provider_model="openrouter-deepseek/deepseek-v4-flash-latest",
        level=1,
        system_prompt="test",
        output_model=_FakeOutput,
        user_message="hello",
        label="test",
        what="testing",
        exc_type=RuntimeError,
        output_retries=4,
    )

    mock_get_prov.assert_called_once()
    mock_provider = mock_get_prov.return_value
    mock_provider.build_agent.assert_called_once()
    _call_kwargs = mock_provider.build_agent.call_args.kwargs
    assert _call_kwargs["retries"] == 4


def test_output_retries_none_passes_default_retries(mock_deps):
    """When ``output_retries`` is ``None`` (default), ``retries=2``
    (the provider default) is passed explicitly to ``build_agent``."""
    _mock_key, mock_get_prov, _mock_run = mock_deps

    _run_llm_agent(
        api_key=None,
        provider_model="openrouter-deepseek/deepseek-v4-flash-latest",
        level=1,
        system_prompt="test",
        output_model=_FakeOutput,
        user_message="hello",
        label="test",
        what="testing",
        exc_type=RuntimeError,
    )

    mock_provider = mock_get_prov.return_value
    mock_provider.build_agent.assert_called_once()
    assert mock_provider.build_agent.call_args.kwargs["retries"] == 2


# ---------------------------------------------------------------------------
# UnexpectedModelBehavior retry
# ---------------------------------------------------------------------------


def test_unexpected_model_behavior_retried_up_to_thrice(mock_deps):
    """First ``UnexpectedModelBehavior`` triggers retries (up to 3);
    the fourth attempt succeeds."""
    _mock_key, _mock_get_prov, mock_run = mock_deps

    mock_run.side_effect = [
        UnexpectedModelBehavior("format slip"),
        UnexpectedModelBehavior("format slip"),
        UnexpectedModelBehavior("format slip"),
        mock.Mock(output=_FakeOutput(value="recovered")),
    ]

    result = _run_llm_agent(
        api_key=None,
        provider_model="openrouter-deepseek/deepseek-v4-flash-latest",
        level=1,
        system_prompt="test",
        output_model=_FakeOutput,
        user_message="hello",
        label="test",
        what="testing",
        exc_type=RuntimeError,
    )

    assert isinstance(result, _FakeOutput)
    assert result.value == "recovered"
    assert mock_run.call_count == 4


def test_unexpected_model_behavior_all_attempts_fail(mock_deps):
    """When all four attempts raise ``UnexpectedModelBehavior``, the
    last is re-raised as ``exc_type``."""
    _mock_key, _mock_get_prov, mock_run = mock_deps

    mock_run.side_effect = UnexpectedModelBehavior("persistent format slip")

    with pytest.raises(RuntimeError, match="persistent format slip"):
        _run_llm_agent(
            api_key=None,
            provider_model="openrouter-deepseek/deepseek-v4-flash-latest",
            level=1,
            system_prompt="test",
            output_model=_FakeOutput,
            user_message="hello",
            label="test",
            what="testing",
            exc_type=RuntimeError,
        )

    assert mock_run.call_count == 4
