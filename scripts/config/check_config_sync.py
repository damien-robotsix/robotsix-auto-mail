#!/usr/bin/env python3
"""Check that config artifacts are in sync with ``MailConfig``.

Cross-references the canonical ``MailConfig`` field list (obtained via
``MailConfig.model_fields``) against two user-facing artifacts:

1. ``docs/config/mail.local.example.yaml`` (the single, canonical multi-account
   example)
2. ``docs/connecting.md`` (the YAML-key table)

Configuration is read only from the YAML config file; environment-variable
configuration has been removed, so no ``.env.example`` surface is checked.

The merged ``docs/config/mail.local.example.yaml`` is checked two ways: its
representative (first) account's nested sections plus the commented optional
sections are cross-referenced against every ``MailConfig`` field (field
drift), and the whole file is validated structurally against
``MailAccountsConfig`` (>= 2 accounts, unique non-empty ids, unique per-account
``db_path``s, a valid ``default_account``, and no account silently falling back
to a legacy ``.data/mail.db`` / ``.data/mail-<id>.db`` default).

Exits 0 when in sync, 1 when drift is found, 2 on a script-level error.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml
from pydantic.fields import PydanticUndefined

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
# Hard-coded field mappings (second pair of eyes on the mapping logic).
# ---------------------------------------------------------------------------

FIELD_TO_YAML: dict[str, str] = {
    "imap_host": "imap.host",
    "imap_port": "imap.port",
    "imap_tls_mode": "imap.tls_mode",
    "imap_folder": "imap.folder",
    "smtp_host": "smtp.host",
    "smtp_port": "smtp.port",
    "smtp_tls_mode": "smtp.tls_mode",
    "username": "auth.username",
    "password": "auth.password",
    "oauth2_token": "auth.oauth2_token",
    "oauth2_client_id": "auth.oauth2_client_id",
    "oauth2_client_secret": "auth.oauth2_client_secret",
    "oauth2_provider": "auth.oauth2_provider",
    "oauth2_tenant": "auth.oauth2_tenant",
    "db_path": "store.path",
    "llm_api_key": "llm.api_key",
    "llm_provider_model": "llm.provider_model",
    "ingest_interval_minutes": "ingest.interval_minutes",
    "archive_root": "archive.root",
    "archive_enabled": "archive.enabled",
    "triage_on_ingest": "triage.on_ingest",
    "triage_rules_path": "triage.rules_path",
    "component_agent_enabled": "component_agent.enabled",
    "langfuse_public_key": "langfuse.public_key",
    "langfuse_secret_key": "langfuse.secret_key",
    "langfuse_base_url": "langfuse.base_url",
    "log_level": "logging.level",
    "log_format": "logging.format",
    "log_file_dir": "logging.file_dir",
}

# ---------------------------------------------------------------------------
# Placeholder patterns — values that are NOT default-mismatches.
# ---------------------------------------------------------------------------

_PLACEHOLDER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^sk-or-v1-…$"),
    re.compile(r"^sk-or-v1-\w+$"),
    re.compile(r"^your-password-here$"),
    re.compile(r"^pk-lf-…$"),
    re.compile(r"^sk-lf-…$"),
    re.compile(r"^https://cloud\.langfuse\.com$"),
]

# ====================================================================
# Self-consistency check
# ====================================================================


def _self_consistency_check() -> None:
    """Verify the YAML mapping dict is 1:1 with ``MailConfig`` fields."""
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


def _is_placeholder(value: str) -> bool:
    """Return True when *value* is a known placeholder string."""
    for pattern in _PLACEHOLDER_PATTERNS:
        if pattern.match(value):
            return True
    return False


_MISSING_SENTINEL = object()


def _get_nested(data: dict[str, Any], path: str) -> Any:
    """Return the value at *path* (dotted, e.g. ``imap.host``) in *data*.

    Returns ``_MISSING_SENTINEL`` when any segment is missing.
    """
    keys = path.split(".")
    for key in keys:
        if not isinstance(data, dict):
            return _MISSING_SENTINEL
        val = data.get(key, _MISSING_SENTINEL)
        if val is _MISSING_SENTINEL:
            return _MISSING_SENTINEL
        data = val
    return data


def _field_default(field_info: Any) -> Any:
    """Return *field_info*'s default, or ``_MISSING_SENTINEL``.

    ``SecretStr`` defaults are unwrapped to plain strings so they can be
    compared with YAML / doc-table values.
    """
    from pydantic import SecretStr as _SecretStr

    if field_info.default is not PydanticUndefined:
        default = field_info.default
        if isinstance(default, _SecretStr):
            return default.get_secret_value()
        return default
    if field_info.default_factory is not None:
        default = field_info.default_factory()
        if isinstance(default, _SecretStr):
            return default.get_secret_value()
        return default
    return _MISSING_SENTINEL


def _values_match(
    artifact_value: Any,
    mailconfig_default: Any,
    *,
    raw_string: str = "",
) -> bool:
    """Return True when *artifact_value* matches the MailConfig default.

    Handles type coercion for commented-out YAML values (which arrive
    as strings) and placeholder tolerance.
    """
    # If the raw string is a known placeholder, skip comparison.
    if raw_string and _is_placeholder(raw_string):
        return True

    # If the value itself looks like a placeholder string, also skip.
    if isinstance(artifact_value, str) and _is_placeholder(artifact_value):
        return True

    # Required fields (MISSING default) — no comparison performed.
    if mailconfig_default is _MISSING_SENTINEL:
        return True

    # Direct match.
    if artifact_value == mailconfig_default:
        return True

    # Artifact value might be a string but the default is a different
    # type (e.g. commented-out port "993" vs default int 993).
    # Try YAML-parsing the string form.
    if isinstance(artifact_value, str):
        try:
            parsed = yaml.safe_load(artifact_value)
        except yaml.YAMLError:
            return False
        if parsed == mailconfig_default:
            return True
        # Handle "993" → "993" (str) vs 993 (int) — already caught
        # by safe_load giving int 993.  Quoted-string values (e.g.
        # '"direct-tls"') are already handled by the parsed == default
        # check above.

    return False


# ====================================================================
# Check 1 — YAML example file
# ====================================================================


def _scan_commented_yaml(text: str) -> dict[str, str]:
    """Extract commented-out ``section.key: value`` pairs from YAML *text*.

    Indentation-agnostic so it works for the multi-account example, where the
    config sections are nested under ``accounts:`` list entries.  An active
    (uncommented) or commented ``<section>:`` header — a lowercase word
    followed by a colon and nothing else — sets the current section; a
    subsequent commented ``<key>: <value>`` line (with a non-empty value) is
    recorded under it.  Keys whose commented value is empty are intentionally
    not supported (they are indistinguishable from a section header), so the
    example writes ``""`` for otherwise-empty optional values.

    Returns a ``{dotted.path: raw_value_string}`` dict.
    """
    result: dict[str, str] = {}
    current_section: str | None = None

    section_re = re.compile(r"^\s*([a-z][a-z0-9_]*):\s*$")
    commented_section_re = re.compile(r"^\s*#\s*([a-z][a-z0-9_]*):\s*$")
    commented_key_re = re.compile(r"^\s*#\s*([a-z][a-z0-9_]*):\s*(.+)$")

    for line in text.splitlines():
        # Active (uncommented) section header.
        if not line.lstrip().startswith("#"):
            m = section_re.match(line)
            if m:
                current_section = m.group(1)
            continue

        # Commented section header (checked before commented key so an
        # empty-valued line is treated as a header, never a key).
        m = commented_section_re.match(line)
        if m:
            current_section = m.group(1)
            continue

        m = commented_key_re.match(line)
        if m and current_section is not None:
            key = m.group(1)
            value = m.group(2).strip()
            result[f"{current_section}.{key}"] = value

    return result


# ``store.path`` is intentionally per-account in the multi-account example
# (``.data/<id>/mail.db``) and therefore legitimately differs from the
# single-account ``MailConfig`` default — its presence is checked (field
# drift) but its value is not compared against the default.
_SKIP_DEFAULT_CHECK: frozenset[str] = frozenset({"store.path"})


def check_yaml_example(
    text: str,
    path: str = "docs/config/mail.local.example.yaml",
) -> list[dict[str, Any]]:
    """Check the merged multi-account example *text* against ``MailConfig``.

    The representative (first) account's structured sections plus the file's
    commented optional sections are cross-referenced against every
    ``FIELD_TO_YAML`` key, so adding a new ``MailConfig`` field without
    reflecting it in the example still fails the gate.  Commented defaults are
    additionally value-checked against the ``MailConfig`` defaults.

    Returns a list of finding dicts.
    """
    findings: list[dict[str, Any]] = []

    # -- structured parse ---------------------------------------------------
    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError:
        return [{"artifact": path, "type": "yaml-parse-error"}]

    if not isinstance(data, dict):
        return [{"artifact": path, "type": "yaml-parse-error"}]

    accounts = data.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        return [
            {
                "artifact": path,
                "type": "yaml-not-multi-account",
                "message": "expected a non-empty top-level 'accounts:' list",
            }
        ]
    representative = accounts[0]
    if not isinstance(representative, dict):
        return [{"artifact": path, "type": "yaml-parse-error"}]

    # -- structured keys of the representative account ----------------------
    structured: dict[str, Any] = {}

    def _collect_paths(d: dict[str, Any], prefix: str) -> None:
        for k, v in d.items():
            full = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                _collect_paths(v, full)
            else:
                structured[full] = v

    for section, value in representative.items():
        if isinstance(value, dict):
            _collect_paths(value, section)

    # -- top-level keys (llm, langfuse, logging) ----------------------------
    for tls in ("llm", "langfuse", "logging"):
        tls_data = data.get(tls)
        if isinstance(tls_data, dict):
            for k, v in tls_data.items():
                if not isinstance(v, dict):
                    structured[f"{tls}.{k}"] = v

    # -- text scan (commented-out optional keys) ----------------------------
    commented = _scan_commented_yaml(text)

    # -- check each MailConfig field ----------------------------------------
    field_defaults: dict[str, Any] = {}
    for field_name, field_info in MailConfig.model_fields.items():
        field_defaults[field_name] = _field_default(field_info)

    for field_name, ypath in FIELD_TO_YAML.items():
        has_structured = ypath in structured
        has_commented = ypath in commented

        if not has_structured and not has_commented:
            findings.append(
                {
                    "artifact": path,
                    "type": "missing-from-yaml",
                    "key": ypath,
                    "field": field_name,
                }
            )
            continue

        default = field_defaults[field_name]
        if default is _MISSING_SENTINEL:
            continue  # required field — presence check was enough
        if ypath in _SKIP_DEFAULT_CHECK:
            continue  # per-account value; presence check was enough

        if has_structured:
            actual = structured[ypath]
            if not _values_match(actual, default):
                findings.append(
                    {
                        "artifact": path,
                        "type": "default-mismatch",
                        "key": ypath,
                        "expected": default,
                        "actual": actual,
                    }
                )
        elif has_commented:
            raw = commented[ypath]
            if not _values_match(raw, default, raw_string=raw):
                findings.append(
                    {
                        "artifact": path,
                        "type": "default-mismatch",
                        "key": ypath,
                        "expected": default,
                        "actual": raw,
                    }
                )

    # -- stale YAML keys ----------------------------------------------------
    # Any leaf ``section.key`` (structured or commented) that does not map to
    # a known ``MailConfig`` field is flagged so an example cannot document a
    # key the code no longer reads.
    known_paths = set(FIELD_TO_YAML.values())
    for ypath in {*structured, *commented}:
        if "." not in ypath:
            # Top-level account keys (``id``, ``label``) are not config fields.
            continue
        if ypath not in known_paths:
            findings.append(
                {
                    "artifact": path,
                    "type": "stale-yaml-key",
                    "key": ypath,
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

    Returns ``_MISSING_SENTINEL`` when the doc says "-" (none).
    """
    stripped = _strip_backticks(raw.strip())
    if stripped in ("–", "—", "-", "N/A", ""):  # noqa: RUF001
        return _MISSING_SENTINEL
    # YAML-parse: bare numbers, quoted strings, etc.
    try:
        return yaml.safe_load(stripped)
    except yaml.YAMLError:
        return stripped


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

    field_defaults: dict[str, Any] = {}
    for field_name, field_info in MailConfig.model_fields.items():
        field_defaults[field_name] = _field_default(field_info)

    # -- YAML keys table ----------------------------------------------------

    # Map YAML path → row data.
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
        if default is _MISSING_SENTINEL:
            continue

        row = yaml_table[ypath]
        doc_default_raw = row.get("Default", "")
        doc_default = _normalise_doc_default(doc_default_raw)

        if doc_default is _MISSING_SENTINEL:
            # Doc says "-" → no default documented.  Treat an
            # empty-string MailConfig default as equivalent ("no value").
            if default == "":
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

        if doc_default != default:
            findings.append(
                {
                    "artifact": path,
                    "type": "doc-default-mismatch",
                    "key": ypath,
                    "expected": default,
                    "actual": doc_default_raw,
                }
            )

    # Stale YAML rows.
    for ypath in yaml_table:
        if ypath not in FIELD_TO_YAML.values():
            findings.append(
                {
                    "artifact": path,
                    "type": "doc-stale-yaml-key",
                    "key": ypath,
                }
            )

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
        config = MailAccountsConfig.from_yaml(load_path)
    except (ConfigurationError, FileNotFoundError) as exc:
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

    # Per-account db_paths: unique.
    db_paths = [account.config.db_path for account in config.accounts]
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
    text_or_path: str | Path,
    path: str = "docs/config/mail.local.example.yaml",
) -> list[dict[str, Any]]:
    """Check the multi-account example against ``MailAccountsConfig``.

    *text_or_path* may be either a filesystem path to the example file or
    the raw YAML text (which is written to a temporary file so the
    path-based ``MailAccountsConfig.from_yaml`` loader can read it).  *path*
    is the artifact label used in the returned finding dicts.

    Returns a list of finding dicts (empty when the example is consistent).
    """
    source = Path(text_or_path)
    if source.exists():
        return _check_accounts_path(source, path)

    # Treat the argument as YAML text.
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_file = Path(tmp_dir) / "mail.local.example.yaml"
        tmp_file.write_text(str(text_or_path))
        return _check_accounts_path(tmp_file, path)


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
    yaml_path = repo_root / "docs/config" / "mail.local.example.yaml"
    docs_path = repo_root / "docs" / "connecting.md"

    try:
        yaml_text = yaml_path.read_text()
    except FileNotFoundError:
        print(
            f"ERROR: {yaml_path} not found — cannot run YAML check",
            file=sys.stderr,
        )
        return 2
    except OSError as exc:
        print(f"ERROR: cannot read {yaml_path}: {exc}", file=sys.stderr)
        return 2

    try:
        docs_text = docs_path.read_text()
    except FileNotFoundError:
        print(
            f"ERROR: {docs_path} not found — cannot run docs check",
            file=sys.stderr,
        )
        return 2
    except OSError as exc:
        print(f"ERROR: cannot read {docs_path}: {exc}", file=sys.stderr)
        return 2

    # -- run checks ---------------------------------------------------------
    findings: list[dict[str, Any]] = []
    findings.extend(check_yaml_example(yaml_text, str(yaml_path)))
    findings.extend(check_docs_connecting(docs_text, str(docs_path)))
    findings.extend(check_accounts_example(yaml_path, str(yaml_path)))

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
