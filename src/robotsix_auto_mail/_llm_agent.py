"""Shared LLM agent build-and-run helper.

Extracts the resolve → build → run boilerplate that is duplicated across
the codebase so every call site delegates to a single implementation.

The ``pydantic_ai`` and ``robotsix_llmio`` imports are **lazy** (inside
the function body) to keep module-load time low and to preserve the
test-patch surface at ``robotsix_llmio.core.get_provider_for_identifier``
(called internally by ``get_provider_for_identifier``) and
``robotsix_llmio.core.run_agent``.
"""

from __future__ import annotations

import typing

import pydantic
from robotsix_llmio.core import Tier

from robotsix_auto_mail.config import (
    ConfigurationError,
    resolve_llm_api_key,
)


def _run_llm_agent[T: pydantic.BaseModel](
    *,
    api_key: str | None,
    provider_model: str | None,
    tier: Tier,
    system_prompt: str,
    output_model: type[T],
    user_message: str,
    label: str,
    what: str,
    exc_type: type[Exception],
) -> T:
    """Resolve credentials, build an LLM agent, run it, and return its output.

    Args:
        api_key: OpenRouter API key.  ``None`` falls back to the
            standard resolution cascade (env → config file).
        provider_model: LLM provider-model identifier.  ``None`` falls
            back to the tier-level default model from the configured tier.
        tier: LLM tier — ``Tier.CHEAP`` maps to ``level=1``; any other
            tier maps to ``level=2``.
        system_prompt: The system prompt for ``build_agent``.
        output_model: A **plain** ``pydantic.BaseModel`` subclass (NOT
            a ``PromptedOutput`` instance).  The helper wraps it in
            ``PromptedOutput`` internally.
        user_message: The user-facing message to send to the LLM.
        label: Short label for tracing (passed to ``run_agent``).
        what: Human-readable description of the operation (passed to
            ``run_agent``).
        exc_type: Exception class to raise on any failure.  Must
            accept a single string argument.

    Returns:
        The validated output model instance.

    Raises:
        *exc_type*: On a missing API key, an invalid LLM response, or
            any other error during resolution / agent construction /
            execution.
    """
    # -- resolve API key --
    try:
        resolved_key = resolve_llm_api_key(api_key)
    except ConfigurationError as exc:
        raise exc_type(str(exc)) from exc

    # -- lazy imports so the rest of the CLI works without the
    #    LLM provider extra and so test patches can intercept --
    from pydantic_ai import PromptedOutput
    from robotsix_llmio.config.tier import (
        LEVEL1_DEFAULT,
        LEVEL2_DEFAULT,
        LEVEL3_DEFAULT,
        TierConfig,
    )
    from robotsix_llmio.core import (
        get_provider_for_identifier as _get_provider,
    )
    from robotsix_llmio.core import (
        run_agent,
    )

    # -- build agent --
    _tier_config = TierConfig(
        level1=LEVEL1_DEFAULT, level2=LEVEL2_DEFAULT, level3=LEVEL3_DEFAULT
    )
    _level = 1 if tier == Tier.CHEAP else 2
    _tlc = _tier_config.for_level(_level)
    model_id = provider_model if provider_model else _tlc.model
    model_provider = _get_provider(
        model_id, **{**_tlc.provider_kwargs, "api_key": resolved_key}
    )
    agent_handle = model_provider.build_agent(
        level=_level,
        system_prompt=system_prompt,
        output_type=PromptedOutput(output_model),
    )

    # -- call LLM --
    try:
        result = run_agent(
            agent_handle,
            lambda: agent_handle.run_sync(user_message),
            label=label,
            what=what,
            trace_input=user_message,
        )
    except Exception as exc:
        raise exc_type(str(exc)) from exc

    return typing.cast(T, result.output)
