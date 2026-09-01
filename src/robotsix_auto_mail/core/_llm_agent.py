"""Shared LLM agent build-and-run helper.

Extracts the resolve → build → run boilerplate that is duplicated across
the codebase so every call site delegates to a single implementation.

The ``pydantic_ai`` and ``robotsix_llmio`` imports are **lazy** (inside
the function body) to keep module-load time low and to preserve the
test-patch surface at ``robotsix_llmio.core.get_provider_for_identifier``
and ``robotsix_llmio.core.run_agent``. Calls run under llmio's provider
failover (``call_with_failover``): the same capability level is retried on
the fallback provider slot when the default slot fails in a
provider-shaped way.
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
            standard resolution cascade (env → config file).  Only
            required when a call actually resolves to the OpenRouter
            provider — the default (Claude SDK) slot is keyless.
        provider_model: LLM provider-model identifier.  ``None`` uses
            the llmio default for *level*; otherwise it overrides the
            *default* provider slot's binding at *level*.
        level: Capability level — ``1`` cheap/frequent, ``2`` workhorse,
            ``3`` frontier.
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
    # -- resolve API key (non-raising: the default Claude SDK slot is
    #    keyless, so a missing OpenRouter key only matters if a call
    #    actually resolves to the OpenRouter provider) --
    try:
        resolved_key = resolve_llm_api_key(api_key, raise_on_missing=False)
    except ConfigurationError as exc:  # pragma: no cover — non-raising path
        raise exc_type(str(exc)) from exc

    # -- lazy imports so the rest of the CLI works without the
    #    LLM provider extra and so test patches can intercept --
    from pydantic_ai import PromptedOutput
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    from robotsix_llmio.config import load_tier_config
    from robotsix_llmio.core import (
        get_provider_for_identifier as _get_provider,
    )
    from robotsix_llmio.core import (
        run_agent,
    )
    from robotsix_llmio.core.failover import call_with_failover

    # -- tier config: an explicit provider_model overrides the DEFAULT
    #    slot's binding at this level; the fallback slot stays baked so
    #    provider failover keeps working --
    overrides: dict[str, object] = {}
    if provider_model:
        overrides = {"default": {f"level{level}": {"model": provider_model}}}
    try:
        _tier_config = load_tier_config(overrides)
    except Exception as exc:
        raise exc_type(str(exc)) from exc

    def _fn_factory(tlc: typing.Any) -> typing.Callable[[], typing.Any]:
        def _call() -> typing.Any:
            kwargs: dict[str, typing.Any] = dict(tlc.provider_kwargs)
            if tlc.max_tokens is not None:
                kwargs.setdefault("max_tokens", tlc.max_tokens)
            if tlc.provider == "openrouter":
                if not resolved_key:
                    raise exc_type(
                        "An OpenRouter API key is required for the "
                        f"OpenRouter-backed model {tlc.model!r} but none is "
                        "configured (add openrouter.keys.robotsix-auto-mail "
                        "to the config file)."
                    )
                kwargs["api_key"] = resolved_key
            model_provider = _get_provider(tlc.model, **kwargs)
            # Bounded retry on UnexpectedModelBehavior (an output-format
            # flake, not a provider outage): a fresh agent per attempt.
            for _attempt in range(4):
                agent_handle = model_provider.build_agent(
                    level=level,
                    system_prompt=system_prompt,
                    output_type=PromptedOutput(output_model),
                    retries=output_retries if output_retries is not None else 2,
                )

                def _run_once(h: typing.Any = agent_handle) -> typing.Any:
                    return h.run_sync(user_message)

                try:
                    return run_agent(
                        agent_handle,
                        _run_once,
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
                    raise
            raise AssertionError("unreachable")  # pragma: no cover

        return _call

    # -- call LLM with provider failover: same level, other slot, when the
    #    active provider fails in a provider-shaped way --
    try:
        result = call_with_failover(
            _fn_factory,
            tier_config=_tier_config,
            level=level,
            what=what,
        )
    except exc_type:
        raise
    except Exception as exc:
        raise exc_type(str(exc)) from exc
    return typing.cast(_T, result.output)
