"""Tests for shared pydantic validators and constants."""

from __future__ import annotations

import pytest

from robotsix_auto_mail.config.pydantic_utils import (
    VALID_CONFIDENCE_LEVELS,
    validate_confidence,
)


class TestValidateConfidence:
    """Tests for :func:`validate_confidence`."""

    @pytest.mark.parametrize("value", ["low", "medium", "high"])
    def test_valid_values_return_themselves(self, value: str) -> None:
        """Valid confidence levels are returned unchanged."""
        assert validate_confidence(value) == value

    def test_invalid_value_raises_value_error(self) -> None:
        """An unrecognised confidence level raises ValueError."""
        with pytest.raises(ValueError, match="confidence must be one of"):
            validate_confidence("very-high")

    def test_empty_string_raises_value_error(self) -> None:
        """An empty string is not a valid confidence level."""
        with pytest.raises(ValueError, match="confidence must be one of"):
            validate_confidence("")

    def test_case_mismatch_raises_value_error(self) -> None:
        """Case-sensitive validation — uppercase variants are rejected."""
        with pytest.raises(ValueError, match="confidence must be one of"):
            validate_confidence("LOW")


class TestValidConfidenceLevels:
    """Tests for the :data:`VALID_CONFIDENCE_LEVELS` constant."""

    def test_contains_exactly_three_values(self) -> None:
        """The frozenset contains exactly the three expected levels."""
        assert VALID_CONFIDENCE_LEVELS == {"low", "medium", "high"}

    def test_is_frozenset(self) -> None:
        """The constant is a frozenset, not a mutable set."""
        assert isinstance(VALID_CONFIDENCE_LEVELS, frozenset)
