"""Shared pydantic validators and constants used across subpackages.

Avoids duplicating the confidence-level constant and its validator
in every module that defines a pydantic model with a ``confidence`` field.
"""

from __future__ import annotations

#: Accepted confidence levels for LLM-generated classifications.
#:
#: Canonical definition — import from here rather than redefining locally.
VALID_CONFIDENCE_LEVELS: frozenset[str] = frozenset({"low", "medium", "high"})


def validate_confidence(v: str) -> str:
    """Validate that *v* is an accepted confidence level.

    Raises:
        ValueError: If *v* is not one of :data:`VALID_CONFIDENCE_LEVELS`.
    """
    if v not in VALID_CONFIDENCE_LEVELS:
        raise ValueError(
            "confidence must be one of "
            f"{sorted(VALID_CONFIDENCE_LEVELS)!r}; got {v!r}"
        )
    return v
