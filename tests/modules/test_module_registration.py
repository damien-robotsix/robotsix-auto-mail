"""Tests for ``scripts/modules/check_module_registration.py``."""

from __future__ import annotations

import sys
from pathlib import Path

# Make the script importable.
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts" / "modules"
sys.path.insert(0, str(_SCRIPTS))

from check_module_registration import (  # noqa: E402
    compute_findings,
    load_registered_paths,
    run_checks,
    suggest_module,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ====================================================================
# Real-repo regression guard
# ====================================================================


def test_real_repo_is_fully_registered() -> None:
    """The actual repository passes the completeness check (exit 0)."""
    assert run_checks(_REPO_ROOT) == 0


# ====================================================================
# Detection logic — small fixtures
# ====================================================================

_MODULES_YAML = """\
modules:
  - id: alpha
    description: Alpha module.
    paths:
      - src/robotsix_auto_mail/alpha.py
      - tests/alpha/test_alpha.py
  - id: beta
    description: Beta module.
    paths:
      - src/robotsix_auto_mail/beta.py
"""


def _registered() -> dict[str, list[str]]:
    return load_registered_paths(_MODULES_YAML)


def test_unclassified_flagged() -> None:
    """A tracked file absent from every module's paths is flagged."""
    tracked = [
        "src/robotsix_auto_mail/alpha.py",
        "tests/alpha/test_alpha.py",
        "src/robotsix_auto_mail/beta.py",
        "src/robotsix_auto_mail/gamma.py",  # unregistered
    ]
    findings = compute_findings(tracked, _registered(), path_exists=lambda p: True)
    assert any(
        f["type"] == "unclassified" and f["path"] == "src/robotsix_auto_mail/gamma.py"
        for f in findings
    )


def test_stale_flagged() -> None:
    """A registered path missing on disk is flagged as stale."""
    tracked = [
        "src/robotsix_auto_mail/alpha.py",
        "tests/alpha/test_alpha.py",
        "src/robotsix_auto_mail/beta.py",
    ]
    # beta.py does not exist on disk in this fixture.
    findings = compute_findings(
        tracked,
        _registered(),
        path_exists=lambda p: p != "src/robotsix_auto_mail/beta.py",
    )
    assert any(
        f["type"] == "stale" and f["path"] == "src/robotsix_auto_mail/beta.py"
        for f in findings
    )


def test_duplicate_flagged() -> None:
    """A path registered under two modules is flagged as duplicate."""
    yaml_text = """\
modules:
  - id: alpha
    description: Alpha module.
    paths:
      - shared/thing.py
  - id: beta
    description: Beta module.
    paths:
      - shared/thing.py
"""
    registered = load_registered_paths(yaml_text)
    findings = compute_findings(
        ["shared/thing.py"], registered, path_exists=lambda p: True
    )
    dup = [f for f in findings if f["type"] == "duplicate"]
    assert dup and dup[0]["path"] == "shared/thing.py"
    assert set(dup[0]["modules"]) == {"alpha", "beta"}


def test_clean_inputs_produce_no_findings() -> None:
    """No findings when every tracked file is registered exactly once."""
    tracked = [
        "src/robotsix_auto_mail/alpha.py",
        "tests/alpha/test_alpha.py",
        "src/robotsix_auto_mail/beta.py",
    ]
    findings = compute_findings(tracked, _registered(), path_exists=lambda p: True)
    assert findings == []


def test_suggest_module_best_effort() -> None:
    """Module suggestion maps a tests/<x>/ path to its source module."""
    suggestion = suggest_module("tests/alpha/test_alpha.py", _registered())
    assert suggestion == "alpha"
    # Unknown paths must not raise and return None.
    assert suggest_module("totally/unknown/file.txt", _registered()) is None
