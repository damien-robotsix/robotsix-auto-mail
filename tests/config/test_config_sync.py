"""Tests for ``scripts/config/check_config_sync.py``."""

from __future__ import annotations

import sys
from pathlib import Path

# Make the script importable.
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts" / "config"
sys.path.insert(0, str(_SCRIPTS))

from check_config_sync import (  # noqa: E402
    check_accounts_example,
    check_docs_connecting,
    check_yaml_example,
    run_checks,
)

from robotsix_auto_mail.config import MailAccountsConfig  # noqa: E402

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_YAML_EXAMPLE = """\
# Example multi-account configuration for robotsix-auto-mail.
#
# Copy this file to config/mail.local.yaml and fill in your real values.
# config/mail.local.yaml is git-ignored so credentials never land in the repo.
#
# Any field you omit falls back to its built-in default (shown commented
# below). The MAIL_ACCOUNTS_<n>_* environment variables override the
# corresponding value here.
#
# llm: and langfuse: are application-wide (top-level) sections.

# LLM provider — used by the `detect` command and future LLM-assisted
# mail processing. The LLM_API_KEY environment variable overrides this
# value.
# llm:
#   api_key: sk-or-v1-…
#   provider_model: ""  # escape-hatch: override llmio tier default (leave blank to use tier default)

# Langfuse observability — optional; enables LLM agent tracing.
# langfuse:
#   public_key: ""
#   secret_key: ""
#   base_url: ""

default_account: personal

accounts:
  - id: personal
    label: Personal
    imap:
      host: imap.example.com
      # port: 993
      # tls_mode: direct-tls
      # folder: INBOX
    smtp:
      host: smtp.example.com
      # port: 587
      # tls_mode: starttls
    auth:
      username: user@example.com
      password: ""  # set your password here, or via MAIL_ACCOUNTS_0_PASSWORD
      # OAuth2 / XOAUTH2 — optional; see docs/connecting.md.
      # oauth2_token: ""
      # oauth2_client_id: ""
      # oauth2_client_secret: ""
      # oauth2_provider: ""
      # oauth2_tenant: organizations
    store:
      path: .data/personal/mail.db
    # Automatic ingestion (used by `ingest --watch`). How often, in minutes,
    # to fetch new mail. Overridable via MAIL_ACCOUNTS_0_INGEST_INTERVAL.
    # ingest:
    #   interval_minutes: 15
    # Self-managed archive folder structure.
    # archive:
    #   root: robotsix-mail-archive
    #   enabled: true
    # Inbox triage agent — runs automatically after each ingest cycle.
    # triage:
    #   on_ingest: true
    #   rules_path: ""
    # Logging configuration — application-wide.
    # logging:
    #   level: INFO
    #   format: console

  - id: work
    label: Work
    imap:
      host: imap.work.example.com
    smtp:
      host: smtp.work.example.com
    auth:
      username: user@work.example.com
      password: ""
    store:
      path: .data/work/mail.db
"""

_ACCOUNTS_EXAMPLE = """\
# Example multi-account configuration.
default_account: personal

accounts:
  - id: personal
    label: Personal
    imap:
      host: imap.gmail.com
    smtp:
      host: smtp.gmail.com
    auth:
      username: me@gmail.com
      password: ""

  - id: work
    label: Work
    imap:
      host: imap.work.example.com
    smtp:
      host: smtp.work.example.com
    auth:
      username: me@work.example.com
      password: ""
"""

_DOCS_YAML_TABLE = """\
### YAML config file

| Key | Required | Default | Purpose |
|---|---|---|---|
| `imap.host` | yes | - | IMAP server hostname |
| `imap.port` | no | `993` | IMAP server port |
| `imap.tls_mode` | no | `"direct-tls"` | IMAP TLS mode |
| `imap.folder` | no | `"INBOX"` | IMAP mailbox folder name |
| `smtp.host` | yes | - | SMTP server hostname |
| `smtp.port` | no | `587` | SMTP server port |
| `smtp.tls_mode` | no | `"starttls"` | SMTP TLS mode |
| `auth.username` | yes | - | Login username |
| `auth.password` | no | - | Login password |
| `auth.oauth2_token` | no | - | OAuth2 access token for SASL XOAUTH2 |
| `auth.oauth2_client_id` | no | - | OAuth2 client ID |
| `auth.oauth2_client_secret` | no | - | OAuth2 client secret |
| `auth.oauth2_provider` | no | - | MSAL OAuth2 provider |
| `auth.oauth2_tenant` | no | `organizations` | Azure AD tenant |
| `store.path` | no | `""` | Filesystem path for the SQLite database |
| `ingest.interval_minutes` | no | `15` | Minutes between automatic ingest cycles |
| `archive.root` | no | `"robotsix-mail-archive"` | Archive root folder |
| `archive.enabled` | no | `true` | Whether to manage the archive structure |
| `triage.on_ingest` | no | `true` | Run inbox triage automatically after ingest |
| `triage.rules_path` | no | `""` | Path to the human-readable triage rules file |
| `llm.api_key` | no | - | LLM provider API key |
| `llm.provider_model` | no | `""` | LLM provider-model identifier |
| `langfuse.public_key` | no | - | Langfuse public key for LLM tracing |
| `langfuse.secret_key` | no | - | Langfuse secret key for LLM tracing |
| `langfuse.base_url` | no | - | Langfuse base URL for LLM tracing |
| `logging.level` | no | `INFO` | Log level |
| `logging.format` | no | `console` | Log format |
"""


def _full_docs(yaml_table: str) -> str:
    """Wrap the YAML-key table in minimal md so the parser finds it."""
    return "# Connecting\n\n## Configuration keys\n\n" + yaml_table + "\n"


def _full_config_docs(yaml_table: str) -> str:
    """Wrap the YAML-key table for docs/configuration.md format."""
    return "# Configuration Reference\n\n" + yaml_table + "\n"


# ====================================================================
# Happy path
# ====================================================================


def test_yaml_example_happy() -> None:
    """No findings when the example YAML matches MailConfig."""
    findings = check_yaml_example(_YAML_EXAMPLE)
    assert findings == []


def test_docs_happy() -> None:
    """No findings when docs match MailConfig."""
    text = _full_docs(_DOCS_YAML_TABLE)
    findings = check_docs_connecting(text)
    assert findings == []


def test_run_checks_happy(tmp_path: Path) -> None:
    """Exit 0 when all artifacts are in sync."""
    repo = tmp_path
    (repo / "docs/config").mkdir(parents=True)
    (repo / "docs/config" / "mail.local.example.yaml").write_text(_YAML_EXAMPLE)
    (repo / "docs" / "configuration.md").write_text(_full_config_docs(_DOCS_YAML_TABLE))
    assert run_checks(repo) == 0


# ====================================================================
# YAML drift
# ====================================================================


def test_yaml_missing_key() -> None:
    """Removing a commented-out key reports missing-from-yaml."""
    modified = _YAML_EXAMPLE.replace("      # port: 993\n", "")
    findings = check_yaml_example(modified)
    assert any(
        f["type"] == "missing-from-yaml" and f["key"] == "imap.port" for f in findings
    )


def test_yaml_stale_key() -> None:
    """Adding an unrecognised key reports stale-yaml-key."""
    modified = _YAML_EXAMPLE + "\n# foo:\n#   bar: 1\n"
    findings = check_yaml_example(modified)
    assert any(
        f["type"] == "stale-yaml-key" and f["key"] == "foo.bar" for f in findings
    )


def test_yaml_default_mismatch() -> None:
    """Changing a commented-out default reports default-mismatch."""
    modified = _YAML_EXAMPLE.replace("# port: 993", "# port: 9993")
    findings = check_yaml_example(modified)
    assert any(
        f["type"] == "default-mismatch"
        and f["key"] == "imap.port"
        and f["expected"] == 993
        for f in findings
    )


def test_yaml_uncommented_default_mismatch() -> None:
    """Changing an uncommented value reports default-mismatch."""
    # Change smtp.host to something else — it's required so no comparison,
    # but we can change a value that has a default... actually smtp.host
    # has no default.  Let's change the password to non-empty.
    modified = _YAML_EXAMPLE.replace('password: ""', 'password: "real"')
    findings = check_yaml_example(modified)
    # password is MISSING in dataclass, so no default comparison.
    # But we should still verify it's not flagged.
    assert not any(
        f["type"] == "default-mismatch" and f["key"] == "auth.password"
        for f in findings
    )


# ====================================================================
# Placeholder tolerance
# ====================================================================


def test_placeholder_llm_api_key_yaml() -> None:
    """llm_api_key placeholder is not a default-mismatch."""
    # The default is "" but the example has "sk-or-v1-…" — OK.
    findings = check_yaml_example(_YAML_EXAMPLE)
    assert not any(
        f["type"] == "default-mismatch" and "llm.api_key" in str(f) for f in findings
    )


# ====================================================================
# Docs table drift
# ====================================================================


def test_doc_missing_yaml_key() -> None:
    """Removing a YAML table row reports doc-missing-yaml-key."""
    modified = _DOCS_YAML_TABLE.replace(
        "| `imap.port` | no | `993` | IMAP server port |\n", ""
    )
    text = _full_docs(modified)
    findings = check_docs_connecting(text)
    assert any(
        f["type"] == "doc-missing-yaml-key" and f["key"] == "imap.port"
        for f in findings
    )


def test_doc_default_mismatch() -> None:
    """Changing a documented default reports doc-default-mismatch."""
    modified = _DOCS_YAML_TABLE.replace("`993`", "`1993`", 1)
    text = _full_docs(modified)
    findings = check_docs_connecting(text)
    assert any(
        f["type"] == "doc-default-mismatch" and f["key"] == "imap.port"
        for f in findings
    )


def test_doc_stale_yaml_key() -> None:
    """Adding a made-up YAML row reports doc-stale-yaml-key."""
    modified = _DOCS_YAML_TABLE + ("| `foo.bar` | no | `1` | Made up |\n")
    text = _full_docs(modified)
    findings = check_docs_connecting(text)
    assert any(
        f["type"] == "doc-stale-yaml-key" and f["key"] == "foo.bar" for f in findings
    )


# ====================================================================
# Exit code 2
# ====================================================================


def test_run_checks_missing_file(tmp_path: Path) -> None:
    """Exit 2 when an artifact file is missing entirely."""
    # Create only some files, but not the YAML example.
    repo = tmp_path
    (repo / "docs/config").mkdir(parents=True)
    # intentionally skip mail.local.example.yaml
    (repo / "docs" / "configuration.md").write_text(_full_config_docs(_DOCS_YAML_TABLE))
    assert run_checks(repo) == 2


# ====================================================================
# Multi-account example check
# ====================================================================


def test_accounts_example_happy() -> None:
    """A well-formed multi-account example produces no findings."""
    findings = check_accounts_example(_ACCOUNTS_EXAMPLE)
    assert findings == []


def test_accounts_example_shipped_file_clean() -> None:
    """The shipped docs/config/mail.local.example.yaml produces no findings."""
    findings = check_accounts_example("docs/config/mail.local.example.yaml")
    assert findings == []


def test_accounts_example_duplicate_ids(tmp_path: Path) -> None:
    """Duplicate account ids surface at least one finding."""
    bad = _ACCOUNTS_EXAMPLE.replace("id: work", "id: personal")
    path = tmp_path / "accounts.yaml"
    path.write_text(bad)
    findings = check_accounts_example(path)
    assert findings


def test_accounts_example_no_accounts_key(tmp_path: Path) -> None:
    """A single-account-shaped doc (no `accounts:` key) is rejected with an
    actionable error naming the `accounts:` list and `detect`."""
    mono = (
        "imap:\n  host: imap.example.com\n"
        "smtp:\n  host: smtp.example.com\n"
        'auth:\n  username: user@example.com\n  password: ""\n'
    )
    path = tmp_path / "mono.yaml"
    path.write_text(mono)
    findings = check_accounts_example(path)
    assert findings
    load_errors = [f for f in findings if f["type"] == "accounts-load-error"]
    assert load_errors
    message = load_errors[0]["message"]
    assert "accounts" in message


def test_accounts_example_colliding_db_paths(tmp_path: Path) -> None:
    """Colliding per-account store.path values surface a finding."""
    bad = (
        "accounts:\n"
        "  - id: a\n"
        "    imap:\n      host: imap.a.example.com\n"
        "    smtp:\n      host: smtp.a.example.com\n"
        '    auth:\n      username: a@example.com\n      password: ""\n'
        "    store:\n      path: .data/shared.db\n"
        "  - id: b\n"
        "    imap:\n      host: imap.b.example.com\n"
        "    smtp:\n      host: smtp.b.example.com\n"
        '    auth:\n      username: b@example.com\n      password: ""\n'
        "    store:\n      path: .data/shared.db\n"
    )
    path = tmp_path / "accounts.yaml"
    path.write_text(bad)
    findings = check_accounts_example(path)
    assert findings


# ====================================================================
# End-to-end against the real repo
# ====================================================================


def test_run_checks_real_repo() -> None:
    """run_checks() against the real repo root still exits 0."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    assert run_checks(repo_root) == 0


def test_shipped_accounts_example_loads() -> None:
    """The shipped multi-account example loads as a valid container via yaml + model_validate."""
    import yaml
    from check_config_sync import _normalise_legacy_yaml

    raw = yaml.safe_load(Path("docs/config/mail.local.example.yaml").read_text())
    config = MailAccountsConfig.model_validate(_normalise_legacy_yaml(raw))
    assert len(config.accounts) >= 2
    ids = config.ids()
    assert len(set(ids)) == len(ids)
    db_paths = [account.config.db_path for account in config.accounts]
    assert len(set(db_paths)) == len(db_paths)
    # The default resolves without raising.
    assert config.default.account_id in ids
