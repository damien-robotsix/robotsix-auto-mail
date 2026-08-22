"""Guard: no concrete model identifiers leak into mail source files.

The robotsix fleet's rule is that specific model names live *only* inside
robotsix-llmio — consumers reference capability levels, not concrete
provider-model strings.  This test enforces that rule at the source level
by scanning every ``.py`` file under ``src/`` for known concrete model
identifiers and failing if any are found.
"""

from __future__ import annotations

from pathlib import Path

#: Patterns whose presence under ``src/`` is a defect.
#: Each pattern must be a concrete model-id fragment — not a provider
#: prefix (``claudeSDK``, ``openrouter``), which are llmio's public
#: vocabulary and intentionally appear in mail source.
_FORBIDDEN_MODEL_PATTERNS: tuple[str, ...] = (
    "deepseek/",
    "mimo-",
    "-opus",
    "claude-fable",
    "gpt-",
)


def test_no_concrete_model_ids_in_source() -> None:
    """Every ``.py`` file under ``src/`` must be free of concrete model ids."""
    src_root = Path(__file__).parent.parent / "src"
    violations: list[tuple[str, str]] = []

    for py_file in sorted(src_root.rglob("*.py")):
        # Skip __pycache__ (defensive — rglob won't normally enter it)
        if "__pycache__" in py_file.parts:
            continue
        text = py_file.read_text()
        for pattern in _FORBIDDEN_MODEL_PATTERNS:
            if pattern in text:
                violations.append((str(py_file.relative_to(src_root.parent)), pattern))

    assert not violations, (
        f"Found {len(violations)} concrete model id(s) under src/:\\n"
        + "\\n".join(f"  {path}: {pattern!r}" for path, pattern in violations)
        + "\\n\\nReplace with shape placeholder or level reference (see ticket)."
    )
