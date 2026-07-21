"""Tests for ``scripts/config/check_config_sync.py``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make the script importable.
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts" / "config"
sys.path.insert(0, str(_SCRIPTS))

from check_config_sync import (  # noqa: E402
    check_accounts_example,
    check_docs_connecting,
    check_json_example,
    run_checks,
)

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_JSON_EXAMPLE: dict = {
    "accounts": [
        {
            "account_id": "personal",
            "label": "Personal",
            "config": {
                "imap_host": "imap.example.com",
                "imap_port": 993,
                "imap_tls_mode": "direct-tls",
                "imap_folder": "INBOX",
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "smtp_tls_mode": "starttls",
                "username": "user@example.com",
                "password": "",
                "oauth2_token": "",
                "oauth2_client_id": "",
                "oauth2_client_secret": "",
                "oauth2_provider": "",
                "oauth2_tenant": "organizations",
                "db_path": ".data/personal/mail.db",
                "ingest_interval_minutes": 15,
                "archive_root": "robotsix-mail-archive",
                "archive_enabled": True,
                "triage_on_ingest": True,
                "triage_rules_path": "",
                "llm_api_key": "",
                "llm_provider_model": "",
                "langfuse_public_key": "",
                "langfuse_secret_key": "",
                "langfuse_base_url": "",
                "log_level": "INFO",
                "log_format": "console",
            },
        },
        {
            "account_id": "work",
            "label": "Work",
            "config": {
                "imap_host": "imap.work.example.com",
                "smtp_host": "smtp.work.example.com",
                "username": "user@work.example.com",
                "password": "",
                "imap_port": 993,
                "imap_tls_mode": "direct-tls",
                "smtp_port": 587,
                "smtp_tls_mode": "starttls",
                "db_path": ".data/work/mail.db",
                "imap_folder": "INBOX",
                "llm_api_key": "",
                "llm_provider_model": "",
                "ingest_interval_minutes": 15,
                "archive_root": "robotsix-mail-archive",
                "archive_enabled": True,
                "triage_on_ingest": True,
                "triage_rules_path": "",
                "oauth2_token": "",
                "oauth2_client_id": "",
                "oauth2_client_secret": "",
                "oauth2_provider": "",
                "oauth2_tenant": "organizations",
                "langfuse_public_key": "",
                "langfuse_secret_key": "",
                "langfuse_base_url": "",
                "log_level": "INFO",
                "log_format": "console",
            },
        },
    ],
    "default_account_id": "personal",
}

_ACCOUNTS_EXAMPLE_JSON: dict = {
    "accounts": [
        {
            "account_id": "personal",
            "label": "Personal",
            "config": {
                "imap_host": "imap.gmail.com",
                "smtp_host": "smtp.gmail.com",
                "username": "me@gmail.com",
                "password": "",
                "db_path": ".data/personal/mail.db",
            },
        },
        {
            "account_id": "work",
            "label": "Work",
            "config": {
                "imap_host": "imap.work.example.com",
                "smtp_host": "smtp.work.example.com",
                "username": "me@work.example.com",
                "password": "",
                "db_path": ".data/work/mail.db",
            },
        },
    ],
    "default_account_id": "personal",
}

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


def test_json_example_happy() -> None:
    """No findings when the JSON example matches MailConfig."""
    findings = check_json_example(_JSON_EXAMPLE)
    assert findings == []


def test_docs_happy() -> None:
    """No findings when docs match MailConfig."""
    text = _full_docs(_DOCS_YAML_TABLE)
    findings = check_docs_connecting(text)
    assert findings == []


def test_run_checks_happy(tmp_path: Path) -> None:
    """Exit 0 when all artifacts are in sync."""
    repo = tmp_path
    (repo / "config").mkdir(parents=True)
    (repo / "config" / "config.example.json").write_text(json.dumps(_JSON_EXAMPLE))
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "configuration.md").write_text(_full_config_docs(_DOCS_YAML_TABLE))
    assert run_checks(repo) == 0


# ====================================================================
# JSON example drift
# ====================================================================


def test_json_example_missing_field() -> None:
    """Removing a field from the first account reports missing-from-json-example."""
    modified = json.loads(json.dumps(_JSON_EXAMPLE))
    del modified["accounts"][0]["config"]["imap_port"]
    findings = check_json_example(modified)
    assert any(
        f["type"] == "missing-from-json-example" and f["field"] == "imap_port"
        for f in findings
    )


def test_json_example_stale_key() -> None:
    """Adding an unrecognised key reports stale-json-example-key."""
    modified = json.loads(json.dumps(_JSON_EXAMPLE))
    modified["accounts"][0]["config"]["made_up_field"] = 1
    findings = check_json_example(modified)
    assert any(
        f["type"] == "stale-json-example-key" and f["key"] == "made_up_field"
        for f in findings
    )


def test_json_example_no_accounts() -> None:
    """An empty accounts list reports json-example-no-accounts."""
    findings = check_json_example({"accounts": []})
    assert any(f["type"] == "json-example-no-accounts" for f in findings)


def test_json_example_missing_config() -> None:
    """A first account without a config dict reports json-example-missing-config."""
    findings = check_json_example({"accounts": [{"account_id": "x", "label": "X"}]})
    assert any(f["type"] == "json-example-missing-config" for f in findings)


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
    repo = tmp_path
    # Create only docs/configuration.md, not config/config.example.json.
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "configuration.md").write_text(_full_config_docs(_DOCS_YAML_TABLE))
    assert run_checks(repo) == 2


# ====================================================================
# Multi-account example check
# ====================================================================


def test_accounts_example_happy(tmp_path: Path) -> None:
    """A well-formed multi-account JSON example produces no findings."""
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps(_ACCOUNTS_EXAMPLE_JSON))
    findings = check_accounts_example(str(path))
    assert findings == []


def test_accounts_example_shipped_file_clean() -> None:
    """The shipped config/config.example.json produces no findings."""
    findings = check_accounts_example("config/config.example.json")
    assert findings == []


def test_accounts_example_duplicate_ids(tmp_path: Path) -> None:
    """Duplicate account ids surface at least one finding."""
    bad = json.loads(json.dumps(_ACCOUNTS_EXAMPLE_JSON))
    bad["accounts"][1]["account_id"] = "personal"
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps(bad))
    findings = check_accounts_example(str(path))
    assert findings


def test_accounts_example_no_accounts_key(tmp_path: Path) -> None:
    """A flat object (no ``accounts`` key) is rejected with an
    actionable error naming the ``accounts`` list."""
    mono = {
        "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com",
        "username": "user@example.com",
        "password": "",
    }
    path = tmp_path / "mono.json"
    path.write_text(json.dumps(mono))
    findings = check_accounts_example(str(path))
    assert findings
    load_errors = [f for f in findings if f["type"] == "accounts-load-error"]
    assert load_errors
    message = load_errors[0]["message"]
    assert "accounts" in message


def test_accounts_example_colliding_db_paths(tmp_path: Path) -> None:
    """Colliding per-account store.path values surface a finding."""
    bad: dict = {
        "accounts": [
            {
                "account_id": "a",
                "label": "A",
                "config": {
                    "imap_host": "imap.a.example.com",
                    "smtp_host": "smtp.a.example.com",
                    "username": "a@example.com",
                    "password": "",
                    "db_path": ".data/shared.db",
                },
            },
            {
                "account_id": "b",
                "label": "B",
                "config": {
                    "imap_host": "imap.b.example.com",
                    "smtp_host": "smtp.b.example.com",
                    "username": "b@example.com",
                    "password": "",
                    "db_path": ".data/shared.db",
                },
            },
        ],
        "default_account_id": "a",
    }
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps(bad))
    findings = check_accounts_example(str(path))
    assert findings


# ====================================================================
# End-to-end against the real repo
# ====================================================================


def test_run_checks_real_repo() -> None:
    """run_checks() against the real repo root still exits 0."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    assert run_checks(repo_root) == 0
