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

from robotsix_auto_mail.config import (
    ConfigurationError,
    resolve_llm_api_key,
)

# TypeVar is used instead of PEP 695 ``[T: pydantic.BaseModel]`` to
# avoid a CodeQL ``py/uninitialized-local-variable`` false positive on
# the type parameter.
_T = typing.TypeVar("_T", bound=pydantic.BaseModel)


def _run_llm_agent(  # noqa: UP047
    *,
    api_key: str | None,
    provider_model: str | None,
    level: int,
    system_prompt: str,
    output_model: type[_T],
    user_message: str,
    label: str,
    what: str,
    exc_type: type[Exception],
    output_retries: int | None = None,
) -> _T:
    """Resolve credentials, build an LLM agent, run it, and return its output.

    Args:
        api_key: OpenRouter API key.  ``None`` falls back to the
            standard resolution cascade (env → config file).
        provider_model: LLM provider-model identifier.  ``None`` falls
            back to the tier-level default model from the configured tier.
        level: Integer model tier — ``1`` = cheap (fastest/cheapest),
            ``2`` = default.
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
        output_retries: Maximum retry attempts for the underlying
            pydantic-ai agent's output validation.  ``None`` (default)
            uses the provider's built-in default (currently ``2``).
            Pass an integer to override — e.g. ``3`` or ``4`` for
            inspection-style agents where model-format flakes are
            non-fatal.

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
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    from robotsix_llmio.config.tier import (
        LEVEL1_DEFAULT,
        LEVEL2_DEFAULT,
        LEVEL3_DEFAULT,
        TierConfig,
        TierLevelConfig,
    )
    from robotsix_llmio.core import (
        get_provider_for_identifier as _get_provider,
    )
    from robotsix_llmio.core import (
        run_agent,
    )

    #: Level-4 default model (llmio does not yet define LEVEL4_DEFAULT).
    _level4_default = TierLevelConfig(model="claudeSDK-opus")

    # -- build agent --
    _tier_config = TierConfig(
        level1=LEVEL1_DEFAULT, level2=LEVEL2_DEFAULT, level3=LEVEL3_DEFAULT
    )
    _level = level
    _tlc = _level4_default if _level == 4 else _tier_config.for_level(_level)
    model_id = provider_model if provider_model else _tlc.model
    model_provider = _get_provider(
        model_id, **{**_tlc.provider_kwargs, "api_key": resolved_key}
    )
    agent_handle = model_provider.build_agent(
        level=_level,
        system_prompt=system_prompt,
        output_type=PromptedOutput(output_model),
        retries=output_retries if output_retries is not None else 2,
    )

    # -- call LLM --
    result = None
    for _attempt in range(4):
        try:
            result = run_agent(
                agent_handle,
                lambda: agent_handle.run_sync(user_message),
                label=label,
                what=what,
                trace_input=user_message,
            )
        except UnexpectedModelBehavior as exc:
            if _attempt < 3:
                import logging

                logging.getLogger(__name__).warning(
                    "UnexpectedModelBehavior on attempt %d; retrying: %s",
                    _attempt + 1,
                    exc,
                )
                continue
            raise exc_type(str(exc)) from exc
        except Exception as exc:
            raise exc_type(str(exc)) from exc
        else:
            break

    if result is None:  # pragma: no cover — loop body always sets result
        raise exc_type("LLM call returned no result after all retries")
    return typing.cast(_T, result.output)
