#!/usr/bin/env python3
"""Check that config artifacts are in sync with ``MailConfig``.

Cross-references the canonical ``MailConfig`` field list (obtained via
``MailConfig.model_fields``) against two user-facing artifacts:

1. ``config/config.example.json`` (the canonical multi-account JSON example)
2. ``docs/configuration.md`` (the config-key table)

The JSON example is validated structurally against ``MailAccountsConfig``
(>= 2 accounts, unique non-empty ids, unique per-account ``db_path``s, a
valid ``default_account_id``, and no account silently falling back to a
legacy ``.data/mail.db`` / ``.data/mail-<id>.db`` default).  Every
``MailConfig`` field must appear in the example's first account.

Exits 0 when in sync, 1 when drift is found, 2 on a script-level error.
"""

from __future__ import annotations

import dataclasses
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from pydantic import SecretStr
except ImportError:
    SecretStr = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Make src/ importable both when run directly and when imported by tests.
# ---------------------------------------------------------------------------
_SRC = Path(__file__).resolve().parent.parent.parent / "src"
sys.path.insert(0, str(_SRC))

from robotsix_auto_mail.config import (  # noqa: E402
    ConfigurationError,
    MailAccountsConfig,
    MailConfig,
)

# The legacy single-account flat db path.  Kept as a literal because the
# ``DEFAULT_DB_PATH`` constant no longer exists in the source.
_LEGACY_FLAT_DB_PATH = ".data/mail.db"

# ---------------------------------------------------------------------------
# Field mappings — imported from the canonical shared source
# (``robotsix_auto_mail.config._field_map``).
# ---------------------------------------------------------------------------

from robotsix_auto_mail.config._field_map import (  # noqa: E402
    FIELD_YAML_MAP as FIELD_TO_YAML,
)

# ====================================================================
# Self-consistency check
# ====================================================================


def _self_consistency_check() -> None:
    """Verify the field mapping dict is 1:1 with ``MailConfig`` fields."""
    fields = set(MailConfig.model_fields.keys())

    for field_name in fields:
        if field_name not in FIELD_TO_YAML:
            _fail(
                f"Internal error: field {field_name!r} missing from "
                f"FIELD_TO_YAML mapping"
            )

    for field_name in FIELD_TO_YAML:
        if field_name not in fields:
            _fail(
                f"Internal error: FIELD_TO_YAML key {field_name!r} "
                f"is not a MailConfig field"
            )


# ====================================================================
# Helpers
# ====================================================================


def _fail(message: str) -> None:
    """Print *message* to stderr and exit 2 (script error)."""
    print(message, file=sys.stderr)
    sys.exit(2)


def _field_default(field_name: str) -> Any:
    """Return *field_name*'s default from the pydantic model, or
    ``dataclasses.MISSING``."""
    from pydantic.fields import PydanticUndefined

    field_info = MailConfig.model_fields[field_name]
    if field_info.default not in (None, ..., PydanticUndefined):
        return field_info.default
    if field_info.default_factory is not None:
        return field_info.default_factory()
    return dataclasses.MISSING


def _is_empty_default(default: Any) -> bool:
    """Return True when *default* represents an empty/no-value default.

    Handles plain ``""`` strings as well as :class:`SecretStr` wrapping an
    empty string.
    """
    if default == "":
        return True
    if SecretStr is not None and isinstance(default, SecretStr):
        return default.get_secret_value() == ""
    return False


def _values_match_doc(doc_value: Any, model_default: Any) -> bool:
    """Return True when *doc_value* matches the pydantic model default.

    Like :func:`_values_match` but for the docs-table comparison, where
    *doc_value* has already been normalised by ``_normalise_doc_default``.
    """
    if doc_value == model_default:
        return True
    if SecretStr is not None and isinstance(model_default, SecretStr):
        return doc_value == model_default.get_secret_value()
    return False


# ====================================================================
# Check 1 — JSON example file
# ====================================================================


def check_json_example(
    data: dict[str, Any],
    path: str = "config/config.example.json",
) -> list[dict[str, Any]]:
    """Check the JSON example *data* against ``MailConfig``.

    Every ``MailConfig`` field must appear in the first account's ``config``
    dict.  Returns a list of finding dicts.
    """
    findings: list[dict[str, Any]] = []

    accounts = data.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        findings.append(
            {
                "artifact": path,
                "type": "json-example-no-accounts",
                "message": "expected a non-empty 'accounts' list",
            }
        )
        return findings

    representative = accounts[0]
    if not isinstance(representative, dict):
        findings.append({"artifact": path, "type": "json-example-invalid-account"})
        return findings

    config = representative.get("config")
    if not isinstance(config, dict):
        findings.append(
            {
                "artifact": path,
                "type": "json-example-missing-config",
                "message": "first account must have a 'config' dict",
            }
        )
        return findings

    # -- check each MailConfig field appears in the example -----------------
    for field_name in MailConfig.model_fields:
        if field_name not in config:
            findings.append(
                {
                    "artifact": path,
                    "type": "missing-from-json-example",
                    "field": field_name,
                }
            )

    # -- stale keys in the example ------------------------------------------
    for key in config:
        if key not in MailConfig.model_fields:
            findings.append(
                {
                    "artifact": path,
                    "type": "stale-json-example-key",
                    "key": key,
                }
            )

    return findings


# ====================================================================
# Check 2 — docs/connecting.md
# ====================================================================


def _parse_md_table(text: str, section_heading: str) -> list[dict[str, str]]:
    """Parse the first pipe table after *section_heading* in *text*.

    Returns a list of dicts with keys from the header row.
    """
    # Find the section heading.
    heading_idx = text.find(section_heading)
    if heading_idx == -1:
        return []

    # Find the first table after the heading.  A table starts with a
    # line matching ``| ... |`` followed by a separator line
    # ``|---|...|``.
    rest = text[heading_idx:]
    lines = rest.splitlines()

    table_start: int | None = None
    for i, line in enumerate(lines):
        if line.strip().startswith("|") and "---" not in line:
            # Potential first row.  Check if the next line is a separator.
            if i + 1 < len(lines) and re.match(r"^\s*\|[\s\-:|]+\|\s*$", lines[i + 1]):
                table_start = i
                break

    if table_start is None:
        return []

    # Parse header.
    header_line = lines[table_start]
    headers = [h.strip() for h in header_line.split("|")[1:-1]]

    # Skip header and separator, parse data rows.
    rows: list[dict[str, str]] = []
    for line in lines[table_start + 2 :]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        cells = [c.strip() for c in stripped.split("|")[1:-1]]
        if len(cells) != len(headers):
            continue
        row = dict(zip(headers, cells, strict=True))
        rows.append(row)

    return rows


def _strip_backticks(s: str) -> str:
    """Remove surrounding backtick quotes from *s*."""
    if s.startswith("`") and s.endswith("`"):
        return s[1:-1]
    return s


def _normalise_doc_default(raw: str) -> Any:
    """Convert a documented default value to a Python object.

    Returns ``dataclasses.MISSING`` when the doc says "-" or "*(none)*"
    (none).
    """
    stripped = _strip_backticks(raw.strip())
    if stripped in ("–", "—", "-", "N/A", "", "*(none)*"):  # noqa: RUF001
        return dataclasses.MISSING
    # JSON-parse for booleans, numbers, null; strings must be quoted.
    try:
        return json.loads(stripped)
    except json.JSONDecodeError, ValueError:
        return stripped


def _parse_all_md_tables(text: str) -> list[dict[str, str]]:
    """Parse ALL pipe tables in *text*, returning a flat list of row dicts.

    Unlike ``_parse_md_table``, which finds only the first table after a
    specific heading, this scans the entire text and collects every pipe
    table it encounters.  Each row is a dict keyed by the column headers
    of its table.
    """
    rows: list[dict[str, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("|") and "---" not in line:
            # Potential header row — check next line is a separator.
            if i + 1 < len(lines) and re.match(r"^\s*\|[\s\-:|]+\|\s*$", lines[i + 1]):
                header_line = lines[i]
                headers = [h.strip() for h in header_line.split("|")[1:-1]]
                i += 2  # skip header and separator
                while i < len(lines):
                    row_line = lines[i].strip()
                    if not row_line.startswith("|"):
                        break
                    cells = [c.strip() for c in row_line.split("|")[1:-1]]
                    if len(cells) == len(headers):
                        row = dict(zip(headers, cells, strict=True))
                        rows.append(row)
                    i += 1
                continue
        i += 1
    return rows


# Container-level keys in ``docs/configuration.md`` that are NOT individual
# ``MailConfig`` fields (they describe the ``accounts`` list shape itself).
_CONFIGURATION_MD_CONTAINER_KEYS: frozenset[str] = frozenset(
    {"accounts", "accounts[].id", "accounts[].label", "default_account"}
)


def _validate_yaml_keys_against_mailconfig(
    yaml_rows: list[dict[str, str]],
    path: str,
    *,
    container_keys_to_skip: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Cross-reference parsed YAML-key table rows against FIELD_TO_YAML."""
    findings: list[dict[str, Any]] = []

    field_defaults: dict[str, Any] = {}
    for f_name in MailConfig.model_fields:
        field_defaults[f_name] = _field_default(f_name)

    yaml_table: dict[str, dict[str, str]] = {}
    for row in yaml_rows:
        key_cell = row.get("Key", "")
        ypath = _strip_backticks(key_cell)
        if ypath:
            yaml_table[ypath] = row

    for field_name, ypath in FIELD_TO_YAML.items():
        if ypath not in yaml_table:
            findings.append(
                {
                    "artifact": path,
                    "type": "doc-missing-yaml-key",
                    "key": ypath,
                    "field": field_name,
                }
            )
            continue

        default = field_defaults[field_name]
        if default is dataclasses.MISSING:
            continue

        row = yaml_table[ypath]
        doc_default_raw = row.get("Default", "")
        doc_default = _normalise_doc_default(doc_default_raw)

        if doc_default is dataclasses.MISSING:
            # Doc says "-" / "*(none)*" → no default documented.  Treat an
            # empty-string MailConfig default as equivalent ("no value").
            if _is_empty_default(default):
                continue
            findings.append(
                {
                    "artifact": path,
                    "type": "doc-default-mismatch",
                    "key": ypath,
                    "expected": default,
                    "actual": "(none documented)",
                }
            )
            continue

        if not _values_match_doc(doc_default, default):
            findings.append(
                {
                    "artifact": path,
                    "type": "doc-default-mismatch",
                    "key": ypath,
                    "expected": default,
                    "actual": doc_default_raw,
                }
            )

    for ypath in yaml_table:
        if ypath in container_keys_to_skip:
            continue
        if ypath not in FIELD_TO_YAML.values():
            findings.append(
                {
                    "artifact": path,
                    "type": "doc-stale-yaml-key",
                    "key": ypath,
                }
            )

    return findings


def check_docs_configuration(
    text: str,
    path: str = "docs/configuration.md",
) -> list[dict[str, Any]]:
    """Check *text* (``docs/configuration.md``) against ``MailConfig``.

    Parses every pipe table in the file (the config-key tables are split
    across multiple per-section headings) and cross-references them
    against ``FIELD_TO_YAML``.

    Returns a list of finding dicts.
    """
    findings: list[dict[str, Any]] = []

    yaml_rows = _parse_all_md_tables(text)

    if not yaml_rows:
        findings.append(
            {
                "artifact": path,
                "type": "doc-parse-error",
                "message": "Could not parse any YAML keys tables",
            }
        )

    findings.extend(
        _validate_yaml_keys_against_mailconfig(
            yaml_rows,
            path,
            container_keys_to_skip=_CONFIGURATION_MD_CONTAINER_KEYS,
        )
    )

    return findings


def check_docs_connecting(
    text: str,
    path: str = "docs/connecting.md",
) -> list[dict[str, Any]]:
    """Check *text* (``docs/connecting.md``) against ``MailConfig``.

    Returns a list of finding dicts.
    """
    findings: list[dict[str, Any]] = []

    # -- parse the YAML keys table ------------------------------------------
    yaml_rows = _parse_md_table(text, "### YAML config file")

    if not yaml_rows:
        findings.append(
            {
                "artifact": path,
                "type": "doc-parse-error",
                "message": "Could not parse YAML keys table",
            }
        )

    findings.extend(_validate_yaml_keys_against_mailconfig(yaml_rows, path))

    return findings


# ====================================================================
# Check 3 — structural validation of the merged multi-account example
# ====================================================================

# Legacy per-account default path form (``.data/mail-<id>.db``), replaced by
# the per-account folder form ``.data/<id>/mail.db``.  No account may silently
# fall back to either this or the single-account ``.data/mail.db`` default.
_LEGACY_FLAT_DB_PATH_RE = re.compile(r"^\.data/mail-[^/]+\.db$")


def _check_accounts_path(load_path: Path, label: str) -> list[dict[str, Any]]:
    """Validate the multi-account example loaded from *load_path*.

    *label* is the artifact name used in finding dicts.
    """
    findings: list[dict[str, Any]] = []

    try:
        data = json.loads(load_path.read_text())
        config = MailAccountsConfig.model_validate(data)
    except (
        ConfigurationError,
        FileNotFoundError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        findings.append(
            {
                "artifact": label,
                "type": "accounts-load-error",
                "message": str(exc),
            }
        )
        return findings

    # Must be the *multi*-account example.
    if len(config.accounts) < 2:
        findings.append(
            {
                "artifact": label,
                "type": "accounts-too-few",
                "message": f"expected >= 2 accounts, got {len(config.accounts)}",
            }
        )

    # Account ids: unique and non-empty.
    ids = [account.account_id for account in config.accounts]
    if any(not account_id for account_id in ids):
        findings.append({"artifact": label, "type": "accounts-empty-id"})
    duplicate_ids = sorted({i for i in ids if ids.count(i) > 1})
    if duplicate_ids:
        findings.append(
            {
                "artifact": label,
                "type": "accounts-duplicate-id",
                "key": str(duplicate_ids),
            }
        )

    # Per-account db_paths: unique (skip empty defaults).
    db_paths = [
        account.config.db_path for account in config.accounts if account.config.db_path
    ]
    duplicate_paths = sorted({p for p in db_paths if db_paths.count(p) > 1})
    if duplicate_paths:
        findings.append(
            {
                "artifact": label,
                "type": "accounts-duplicate-db-path",
                "key": str(duplicate_paths),
            }
        )

    # The default account id must resolve to a real account.
    try:
        _ = config.default
    except ConfigurationError as exc:
        findings.append(
            {
                "artifact": label,
                "type": "accounts-bad-default",
                "message": str(exc),
            }
        )

    # No account may fall back to the legacy single-account ".data/mail.db"
    # nor the legacy flat ".data/mail-<id>.db" form; each must use the
    # per-account folder default ".data/<id>/mail.db" (or an explicit path).
    for account in config.accounts:
        db_path = account.config.db_path
        if db_path == _LEGACY_FLAT_DB_PATH or _LEGACY_FLAT_DB_PATH_RE.match(db_path):
            findings.append(
                {
                    "artifact": label,
                    "type": "accounts-legacy-db-path",
                    "key": account.account_id,
                }
            )

    return findings


def check_accounts_example(
    path: str = "config/config.example.json",
) -> list[dict[str, Any]]:
    """Check the multi-account JSON example at *path* against
    ``MailAccountsConfig``.

    Returns a list of finding dicts (empty when the example is consistent).
    """
    return _check_accounts_path(Path(path), path)


# ====================================================================
# Main entry point
# ====================================================================


def _repo_root() -> Path:
    """Return the repo root (parent of the ``scripts/`` directory)."""
    return Path(__file__).resolve().parent.parent.parent


def run_checks(
    repo_root: Path | None = None,
) -> int:
    """Run all config-sync checks.  Returns exit code 0, 1, or 2.

    Args:
        repo_root: Path to the repository root.  Defaults to auto-detection.
    """
    if repo_root is None:
        repo_root = _repo_root()

    # -- self-consistency first ---------------------------------------------
    try:
        _self_consistency_check()
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    # -- load artifact files ------------------------------------------------
    json_example_path = repo_root / "config" / "config.example.json"
    docs_cfg_path = repo_root / "docs" / "configuration.md"

    try:
        json_data = json.loads(json_example_path.read_text())
    except FileNotFoundError:
        print(
            f"ERROR: {json_example_path} not found — cannot run JSON example check",
            file=sys.stderr,
        )
        return 2
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read {json_example_path}: {exc}", file=sys.stderr)
        return 2

    try:
        docs_cfg_text = docs_cfg_path.read_text()
    except FileNotFoundError:
        print(
            f"ERROR: {docs_cfg_path} not found — cannot run docs check",
            file=sys.stderr,
        )
        return 2
    except OSError as exc:
        print(f"ERROR: cannot read {docs_cfg_path}: {exc}", file=sys.stderr)
        return 2

    # -- run checks ---------------------------------------------------------
    findings: list[dict[str, Any]] = []
    findings.extend(check_json_example(json_data, str(json_example_path)))
    findings.extend(check_docs_configuration(docs_cfg_text, str(docs_cfg_path)))
    findings.extend(check_accounts_example(str(json_example_path)))

    # -- report -------------------------------------------------------------
    if not findings:
        print("OK")
        return 0

    for f in findings:
        ftype = f.get("type", "unknown")
        artifact = f.get("artifact", "?")
        key = f.get("key", "?")
        expected = f.get("expected", None)
        actual = f.get("actual", None)
        if expected is not None and actual is not None:
            print(
                f"{artifact}: {ftype}: {key} — expected {expected!r}, got {actual!r}",
                file=sys.stderr,
            )
        else:
            extra = f.get("field", "") or f.get("message", "")
            if extra:
                print(
                    f"{artifact}: {ftype}: {key} ({extra})",
                    file=sys.stderr,
                )
            else:
                print(
                    f"{artifact}: {ftype}: {key}",
                    file=sys.stderr,
                )

    return 1


def main() -> None:
    """Entry point for the console script."""
    sys.exit(run_checks())


if __name__ == "__main__":
    main()
