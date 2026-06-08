#!/usr/bin/env python3
"""Check that every tracked repo file is registered in ``docs/modules.yaml``.

The repository README declares a hard contract: every version-controlled
file must be registered under exactly one module's ``paths`` list in
``docs/modules.yaml``.  This script enforces that contract.

It cross-references the set of version-controlled files (obtained via
``git ls-files``) against the union of every module's ``paths`` entries
and reports three kinds of drift:

* **Unclassified** — a tracked file not present in any module's ``paths``.
* **Stale** — a registered path that does not exist on disk.
* **Duplicate** — a path registered under more than one module.

Exits 0 when clean, 1 when drift is found, 2 on a script-level error
(e.g. cannot read/parse ``docs/modules.yaml``, ``git`` not available).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

# ---------------------------------------------------------------------------
# Repo location helpers.
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Return the repo root (parent of the ``scripts/`` directory)."""
    return Path(__file__).resolve().parent.parent.parent


# ====================================================================
# Inputs
# ====================================================================


def git_tracked_files(repo_root: Path) -> list[str]:
    """Return the repo-relative paths of all version-controlled files.

    Uses ``git ls-files``, which returns exactly the tracked set and
    naturally excludes ``.git/``, ``__pycache__/``, ``.venv/`` and
    untracked build artifacts — so no hand-maintained ignore list is
    needed.

    Raises ``RuntimeError`` when git is unavailable or fails.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files"],  # noqa: S607
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git binary not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"`git ls-files` failed (exit {exc.returncode}): {exc.stderr}"
        ) from exc

    return [line for line in proc.stdout.splitlines() if line.strip()]


def load_registered_paths(yaml_text: str) -> dict[str, list[str]]:
    """Parse ``modules.yaml`` text into a ``{path: [module_id, ...]}`` map.

    A path mapping to more than one module id indicates a duplicate
    registration.

    Raises ``ValueError`` when the document cannot be parsed into the
    expected ``modules: [...]`` shape.
    """
    try:
        data: Any = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"cannot parse modules.yaml: {exc}") from exc

    if not isinstance(data, dict) or "modules" not in data:
        raise ValueError("modules.yaml has no top-level 'modules' list")

    modules = data["modules"]
    if not isinstance(modules, list):
        raise ValueError("modules.yaml 'modules' is not a list")

    result: dict[str, list[str]] = {}
    for module in modules:
        if not isinstance(module, dict):
            raise ValueError("each module entry must be a mapping")
        module_id = str(module.get("id", "<unknown>"))
        paths = module.get("paths") or []
        if not isinstance(paths, list):
            raise ValueError(f"module {module_id!r} 'paths' is not a list")
        for path in paths:
            result.setdefault(str(path), []).append(module_id)

    return result


# ====================================================================
# Suggestion (best-effort)
# ====================================================================


def suggest_module(
    path: str,
    registered: dict[str, list[str]],
) -> str | None:
    """Best-effort guess of the owning module for an unclassified *path*.

    For a ``tests/<x>/...`` or ``tests/test_<x>.py`` file, look up the
    module that owns the corresponding ``src/robotsix_auto_mail/<x>.py``
    source file.  Returns ``None`` when no plausible suggestion exists.
    Never raises.
    """
    try:
        # Build a reverse lookup of source-stem -> module id.
        stem_to_module: dict[str, str] = {}
        for reg_path, module_ids in registered.items():
            if reg_path.startswith("src/robotsix_auto_mail/") and (
                reg_path.endswith(".py")
            ):
                stem = Path(reg_path).stem.lstrip("_")
                if module_ids:
                    stem_to_module[stem] = module_ids[0]

        candidates: list[str] = []
        p = Path(path)
        if path.startswith("tests/"):
            parts = p.parts
            if len(parts) >= 3:
                # tests/<sub>/test_foo.py -> use the <sub> dir name.
                candidates.append(parts[1])
            # tests/test_foo.py -> derive 'foo' from the filename.
            name = p.stem
            if name.startswith("test_"):
                candidates.append(name[len("test_") :])

        for cand in candidates:
            key = cand.lstrip("_")
            if key in stem_to_module:
                return stem_to_module[key]
    except Exception:  # suggestion must never crash the check
        return None

    return None


# ====================================================================
# Findings
# ====================================================================


#: Repo-relative path prefixes that are exempt from the "every file must
#: be registered" contract.  Use for vendored third-party code and
#: tool-runtime artifacts that are not application source modules.
_UNCLASSIFIED_EXEMPT_PREFIXES: tuple[str, ...] = ("pip-packages/",)


def _is_exempt_from_classification(path: str) -> bool:
    """True when *path* is explicitly exempt from module registration."""
    return path.startswith(_UNCLASSIFIED_EXEMPT_PREFIXES)


def compute_findings(
    tracked_files: list[str],
    registered: dict[str, list[str]],
    *,
    path_exists: Callable[[str], bool],
) -> list[dict[str, Any]]:
    """Compute registration-drift findings.

    Args:
        tracked_files: repo-relative version-controlled paths.
        registered: ``{path: [module_id, ...]}`` from ``modules.yaml``.
        path_exists: predicate telling whether a registered path exists
            on disk (used for the stale check).

    Returns a list of finding dicts, each with a ``type`` and ``path``.
    """
    findings: list[dict[str, Any]] = []
    tracked_set = set(tracked_files)

    # -- unclassified: tracked but not registered ---------------------------
    for path in sorted(tracked_set):
        if _is_exempt_from_classification(path):
            continue
        if path not in registered:
            findings.append(
                {
                    "type": "unclassified",
                    "path": path,
                    "suggestion": suggest_module(path, registered),
                }
            )

    # -- stale: registered but missing on disk ------------------------------
    for path in sorted(registered):
        if not path_exists(path):
            findings.append({"type": "stale", "path": path})

    # -- duplicate: registered under more than one module -------------------
    for path in sorted(registered):
        module_ids = registered[path]
        if len(module_ids) > 1:
            findings.append(
                {
                    "type": "duplicate",
                    "path": path,
                    "modules": list(module_ids),
                }
            )

    return findings


def format_finding(finding: dict[str, Any]) -> str:
    """Return a human-readable, file-naming message for *finding*."""
    ftype = finding.get("type", "unknown")
    path = finding.get("path", "?")
    if ftype == "unclassified":
        suggestion = finding.get("suggestion")
        hint = (
            f" (suggested module: {suggestion})"
            if suggestion
            else " (no module suggestion)"
        )
        return (
            f"unclassified: {path} is tracked but not registered in "
            f"any module's paths{hint}"
        )
    if ftype == "stale":
        return f"stale: {path} is registered in modules.yaml but does not exist on disk"
    if ftype == "duplicate":
        modules = ", ".join(finding.get("modules", []))
        return (
            f"duplicate: {path} is registered under more than one module "
            f"({modules}); it must belong to exactly one"
        )
    return f"{ftype}: {path}"


# ====================================================================
# Main entry point
# ====================================================================


def run_checks(repo_root: Path | None = None) -> int:
    """Run the registration-completeness check.  Returns 0, 1, or 2."""
    if repo_root is None:
        repo_root = _repo_root()

    # -- tracked files ------------------------------------------------------
    try:
        tracked_files = git_tracked_files(repo_root)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # -- registered paths ---------------------------------------------------
    modules_path = repo_root / "docs" / "modules.yaml"
    try:
        yaml_text = modules_path.read_text()
    except OSError as exc:
        print(f"ERROR: cannot read {modules_path}: {exc}", file=sys.stderr)
        return 2

    try:
        registered = load_registered_paths(yaml_text)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # -- compute findings ---------------------------------------------------
    findings = compute_findings(
        tracked_files,
        registered,
        path_exists=lambda p: (repo_root / p).exists(),
    )

    if not findings:
        print("OK")
        return 0

    for finding in findings:
        print(format_finding(finding), file=sys.stderr)

    return 1


def main() -> None:
    """Entry point for the console script."""
    sys.exit(run_checks())


if __name__ == "__main__":
    main()
