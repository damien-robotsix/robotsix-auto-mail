#!/usr/bin/env python3
"""CI/pre-commit gate: reject bare string TicketKind literals in test files.

We grep for Python assignment / comparison of ``kind`` with a bare
string ``"task"``, ``"inquiry"``, or ``"epic"`` and fail if any match.
The production code already uses ``TicketKind.TASK`` etc. exclusively;
this prevents regressions in test code.

This repo (robotsix-auto-mail) does not have a ``TicketKind`` enum; the
check is inherited from the shared ``python-ci.yml`` reusable workflow.
It remains here as a no-op gate that will catch any future TicketKind
regressions should this repo adopt the robotsix-mill domain model.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Patterns that match bare string kind usage in non-string-literal contexts.
#
# Matches:  kind="epic"  kind = "task"  .kind == "epic"  ["kind"] == "task"
# Also catches JSON-like dicts:  "kind": "epic"  (common in API test POST bodies).
KIND_BARE_STRING_RE = re.compile(
    rb'(kind\s*[=!]?=\s*"(task|inquiry|epic)")'
    rb'|(\["kind"\]\s*==\s*"(task|inquiry|epic)")'
    rb'|("kind"\s*:\s*"(task|inquiry|epic)")'
)


def _is_skip_line(line: bytes) -> bool:
    """Return True for lines that are documentation-only references."""
    stripped = line.strip()
    if stripped.startswith(b"#"):
        return True
    if stripped.startswith(b'"""') or stripped.endswith(b'"""'):
        return True
    return False


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    test_dir = repo_root / "tests"
    violations: list[tuple[str, int, bytes]] = []

    for py_file in test_dir.rglob("*.py"):
        try:
            lines = py_file.read_bytes().split(b"\n")
        except OSError:
            continue
        for i, line in enumerate(lines, start=1):
            if _is_skip_line(line):
                continue
            if KIND_BARE_STRING_RE.search(line):
                violations.append(
                    (str(py_file.relative_to(repo_root)), i, line.strip())
                )

    if violations:
        print(
            "Bare string TicketKind literals found in test files.\n"
            "Replace with TicketKind.TASK / TicketKind.INQUIRY / TicketKind.EPIC:\n",
            file=sys.stderr,
        )
        for path, lineno, line in violations:
            print(f"  {path}:{lineno}: {line.decode()}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
