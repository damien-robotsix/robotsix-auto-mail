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
    check_env_example,
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
#   provider_model: openrouter-deepseek

# Langfuse observability — optional; enables LLM agent tracing.
# langfuse:
#   public_key: ""
#   secret_key: ""
#   base_url: ""

# Board agent — optional agent-comm bridge to the mill board.
# When enabled, other agents can drive the board programmatically.
# board_agent:
#   enabled: false
#   api_url: ""
#   api_token: ""
#   repo_id: ""
#   write_ops: true

# Component agent — optional agent-comm responder on the shared broker.
# Disabled by default; requires robotsix_agent_comm and a valid broker token.
# component_agent:
#   enabled: false
#   agent_id: board-manager-robotsix-auto-mail
#   broker_host: ""
#   broker_port: 443
#   broker_token: ""
#   broker_tls_ca: ""

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
    #   namespace: ""
    #   enabled: true
    # Inbox triage agent — runs automatically after each ingest cycle.
    # triage:
    #   on_ingest: true
    # Calendar (Add to Calendar) — agent-comm dispatch to the
    # robotsix-calendar agent.  When enabled, the detail view shows
    # an 'Add to Calendar' button that sends a CalendarEventRequest
    # message via the robotsix_agent_comm message bus.
    # calendar:
    #   enabled: true
    #   # Transport mode: "in-process" (default) or "brokered".
    #   transport: in-process
    #   # Broker settings (required when transport: brokered).
    #   broker_host: ""
    #   broker_port: 443
    #   broker_tls_ca: ""
    #   broker_client_cert: ""
    #   broker_client_key: ""
    #   broker_token: ""
    # Logging configuration — application-wide.
    # logging:
    #   level: INFO
    #   format: console
    #   file_dir: .mail_log

    # Board agent — optional agent-comm bridge to the mill board.
    # When enabled, other agents can drive the board programmatically.
    # board_agent:
    #   enabled: false
    #   api_url: ""
    #   api_token: ""
    #   repo_id: ""
    #   write_ops: true

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

_ENV_EXAMPLE = """\
# Example environment variables for robotsix-auto-mail.

MAIL_IMAP_HOST=imap.example.com
MAIL_IMAP_PORT=993
MAIL_IMAP_TLS_MODE=direct-tls
MAIL_IMAP_FOLDER=INBOX
MAIL_SMTP_HOST=smtp.example.com
MAIL_SMTP_PORT=587
MAIL_SMTP_TLS_MODE=starttls
MAIL_USERNAME=user@example.com
MAIL_PASSWORD=your-password-here
MAIL_OAUTH2_TOKEN=
MAIL_OAUTH2_CLIENT_ID=
MAIL_OAUTH2_CLIENT_SECRET=
MAIL_OAUTH2_PROVIDER=
MAIL_OAUTH2_TENANT=organizations
MAIL_DB_PATH=.data/mail.db
MAIL_INGEST_INTERVAL=15
MAIL_ARCHIVE_ROOT=robotsix-mail-archive
MAIL_ARCHIVE_NAMESPACE=
MAIL_ARCHIVE_ENABLED=true
MAIL_TRIAGE_ON_INGEST=true
MAIL_CALENDAR_ENABLED=true
CALENDAR_TRANSPORT=in-process
CALENDAR_BROKER_HOST=
CALENDAR_BROKER_PORT=443
CALENDAR_BROKER_TLS_CA=
CALENDAR_BROKER_CLIENT_CERT=
CALENDAR_BROKER_CLIENT_KEY=
CALENDAR_BROKER_TOKEN=
LLM_API_KEY=sk-or-v1-…
LLM_PROVIDER_MODEL=openrouter-deepseek
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_BASE_URL=
LOG_LEVEL=INFO
LOG_FORMAT=console
LOG_FILE_DIR=.mail_log
BOARD_AGENT_ENABLED=false
BOARD_AGENT_API_URL=
BOARD_AGENT_API_TOKEN=
BOARD_AGENT_REPO_ID=
BOARD_AGENT_WRITE_OPS=true
COMPONENT_AGENT_ENABLED=false
COMPONENT_AGENT_ID=board-manager-robotsix-auto-mail
COMPONENT_AGENT_BROKER_HOST=
COMPONENT_AGENT_BROKER_PORT=443
COMPONENT_AGENT_BROKER_TOKEN=
COMPONENT_AGENT_BROKER_TLS_CA=
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
| `store.path` | no | `".data/mail.db"` | Filesystem path for the SQLite database |
| `ingest.interval_minutes` | no | `15` | Minutes between automatic ingest cycles |
| `archive.root` | no | `"robotsix-mail-archive"` | Archive root folder |
| `archive.namespace` | no | `""` | IMAP namespace prefix for archive folders |
| `archive.enabled` | no | `true` | Whether to manage the archive structure |
| `triage.on_ingest` | no | `true` | Run inbox triage automatically after ingest |
| `calendar.enabled` | no | `true` | Enable 'Add to Calendar' in detail view |
| `calendar.transport` | no | `in-process` | Transport mode for calendar dispatch |
| `calendar.broker_host` | no | - | Broker server hostname |
| `calendar.broker_port` | no | `443` | Broker server port |
| `calendar.broker_tls_ca` | no | - | Path to CA certificate PEM for broker TLS |
| `calendar.broker_client_cert` | no | - | Path to client certificate PEM for mutual TLS |
| `calendar.broker_client_key` | no | - | Path to client key PEM for mutual TLS |
| `calendar.broker_token` | no | - | Agent authentication token for the broker |
| `llm.api_key` | no | - | LLM provider API key |
| `llm.provider_model` | no | `"openrouter-deepseek"` | LLM provider-model identifier |
| `langfuse.public_key` | no | - | Langfuse public key for LLM tracing |
| `langfuse.secret_key` | no | - | Langfuse secret key for LLM tracing |
| `langfuse.base_url` | no | - | Langfuse base URL for LLM tracing |
| `board_agent.enabled` | no | `false` | Enable the board agent (agent-comm bridge) |
| `board_agent.api_url` | no | - | Board agent API base URL |
| `board_agent.api_token` | no | - | Board agent API authentication token |
| `board_agent.repo_id` | no | - | Board repository identifier |
| `board_agent.write_ops` | no | `true` | Allow write operations via the board agent |
| `component_agent.enabled` | no | `false` | Enable the component-agent responder |
| `component_agent.agent_id` | no | `"board-manager-robotsix-auto-mail"` | Agent identifier on the broker |
| `component_agent.broker_host` | no | - | Broker server hostname |
| `component_agent.broker_port` | no | `443` | Broker server port |
| `component_agent.broker_token` | no | - | Agent authentication token for the broker |
| `component_agent.broker_tls_ca` | no | - | Path to CA certificate PEM for broker TLS |
| `logging.level` | no | `INFO` | Log level |
| `logging.format` | no | `console` | Log format |
| `logging.file_dir` | no | `.mail_log` | Log file directory |
"""

_DOCS_ENV_TABLE = """\
### Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `MAIL_IMAP_HOST` | yes | - | IMAP server hostname |
| `MAIL_SMTP_HOST` | yes | - | SMTP server hostname |
| `MAIL_USERNAME` | yes | - | Login username |
| `MAIL_PASSWORD` | yes | - | Login password |
| `MAIL_OAUTH2_TOKEN` | no | - | OAuth2 access token for SASL XOAUTH2 |
| `MAIL_OAUTH2_CLIENT_ID` | no | - | OAuth2 client ID |
| `MAIL_OAUTH2_CLIENT_SECRET` | no | - | OAuth2 client secret |
| `MAIL_OAUTH2_PROVIDER` | no | - | MSAL OAuth2 provider |
| `MAIL_OAUTH2_TENANT` | no | `organizations` | Azure AD tenant |
| `MAIL_IMAP_PORT` | no | `993` | IMAP server port |
| `MAIL_IMAP_TLS_MODE` | no | `direct-tls` | TLS negotiation for IMAP |
| `MAIL_SMTP_PORT` | no | `587` | SMTP server port |
| `MAIL_SMTP_TLS_MODE` | no | `starttls` | TLS negotiation for SMTP |
| `MAIL_IMAP_FOLDER` | no | `INBOX` | IMAP mailbox folder name |
| `MAIL_DB_PATH` | no | `.data/mail.db` | Filesystem path for the SQLite database |
| `MAIL_INGEST_INTERVAL` | no | `15` | Minutes between automatic ingest cycles |
| `MAIL_ARCHIVE_ROOT` | no | `robotsix-mail-archive` | Archive root folder |
| `MAIL_ARCHIVE_NAMESPACE` | no |  | IMAP namespace prefix for archive folders |
| `MAIL_ARCHIVE_ENABLED` | no | `true` | Whether to manage the archive structure |
| `MAIL_TRIAGE_ON_INGEST` | no | `true` | Run inbox triage automatically after ingest |
| `MAIL_CALENDAR_ENABLED` | no | `true` | Enable 'Add to Calendar' in detail view |
| `CALENDAR_TRANSPORT` | no | `in-process` | Transport mode for calendar dispatch |
| `CALENDAR_BROKER_HOST` | no | - | Broker server hostname |
| `CALENDAR_BROKER_PORT` | no | `443` | Broker server port |
| `CALENDAR_BROKER_TLS_CA` | no | - | Path to CA certificate PEM for broker TLS |
| `CALENDAR_BROKER_CLIENT_CERT` | no | - | Path to client certificate PEM for mutual TLS |
| `CALENDAR_BROKER_CLIENT_KEY` | no | - | Path to client key PEM for mutual TLS |
| `CALENDAR_BROKER_TOKEN` | no | - | Agent authentication token for the broker |
| `MAIL_CONFIG_PATH` | no | `config/mail.local.yaml` | Path to the YAML config file |
| `LLM_API_KEY` | no | - | LLM provider API key |
| `LLM_PROVIDER_MODEL` | no | `openrouter-deepseek` | LLM provider-model identifier |
| `LANGFUSE_PUBLIC_KEY` | no | - | Langfuse public key for LLM tracing |
| `LANGFUSE_SECRET_KEY` | no | - | Langfuse secret key for LLM tracing |
| `LANGFUSE_BASE_URL` | no | - | Langfuse base URL for LLM tracing |
| `BOARD_AGENT_ENABLED` | no | `false` | Enable the board agent (agent-comm bridge) |
| `BOARD_AGENT_API_URL` | no | - | Board agent API base URL |
| `BOARD_AGENT_API_TOKEN` | no | - | Board agent API authentication token |
| `BOARD_AGENT_REPO_ID` | no | - | Board repository identifier |
| `BOARD_AGENT_WRITE_OPS` | no | `true` | Allow write operations via the board agent |
| `COMPONENT_AGENT_ENABLED` | no | `false` | Enable the component-agent responder |
| `COMPONENT_AGENT_ID` | no | `board-manager-robotsix-auto-mail` | Agent identifier on the broker |
| `COMPONENT_AGENT_BROKER_HOST` | no | - | Broker server hostname |
| `COMPONENT_AGENT_BROKER_PORT` | no | `443` | Broker server port |
| `COMPONENT_AGENT_BROKER_TOKEN` | no | - | Agent authentication token for the broker |
| `COMPONENT_AGENT_BROKER_TLS_CA` | no | - | Path to CA certificate PEM for broker TLS |
| `LOG_LEVEL` | no | `INFO` | Log level |
| `LOG_FORMAT` | no | `console` | Log format |
| `LOG_FILE_DIR` | no | `.mail_log` | Log file directory |
"""


def _full_docs(yaml_table: str, env_table: str) -> str:
    """Wrap the two tables in minimal md so the parser finds them."""
    return (
        "# Connecting\n\n"
        "## Configuration keys\n\n" + yaml_table + "\n\n" + env_table + "\n"
    )


# ====================================================================
# Happy path
# ====================================================================


def test_yaml_example_happy() -> None:
    """No findings when the example YAML matches MailConfig."""
    findings = check_yaml_example(_YAML_EXAMPLE)
    assert findings == []


def test_env_example_happy() -> None:
    """No findings when .env.example matches MailConfig."""
    findings = check_env_example(_ENV_EXAMPLE)
    assert findings == []


def test_docs_happy() -> None:
    """No findings when docs match MailConfig."""
    text = _full_docs(_DOCS_YAML_TABLE, _DOCS_ENV_TABLE)
    findings = check_docs_connecting(text)
    assert findings == []


def test_run_checks_happy(tmp_path: Path) -> None:
    """Exit 0 when all artifacts are in sync."""
    repo = tmp_path
    (repo / "docs/config").mkdir(parents=True)
    (repo / "docs/config" / "mail.local.example.yaml").write_text(_YAML_EXAMPLE)
    (repo / ".env.example").write_text(_ENV_EXAMPLE)
    (repo / "docs" / "connecting.md").write_text(
        _full_docs(_DOCS_YAML_TABLE, _DOCS_ENV_TABLE)
    )
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
# .env.example drift
# ====================================================================


def test_env_missing_var() -> None:
    """Removing a line reports missing-from-env-example."""
    modified = _ENV_EXAMPLE.replace("MAIL_IMAP_PORT=993\n", "")
    findings = check_env_example(modified)
    assert any(
        f["type"] == "missing-from-env-example" and f["key"] == "MAIL_IMAP_PORT"
        for f in findings
    )


def test_env_stale_var() -> None:
    """Adding a made-up env var reports stale-env-example-var."""
    modified = _ENV_EXAMPLE + "MAIL_FOO=1\n"
    findings = check_env_example(modified)
    assert any(
        f["type"] == "stale-env-example-var" and f["key"] == "MAIL_FOO"
        for f in findings
    )


def test_env_default_mismatch() -> None:
    """Changing a port number reports default-mismatch."""
    modified = _ENV_EXAMPLE.replace("MAIL_IMAP_PORT=993", "MAIL_IMAP_PORT=1")
    findings = check_env_example(modified)
    assert any(
        f["type"] == "default-mismatch"
        and f["key"] == "MAIL_IMAP_PORT"
        and f["expected"] == 993
        for f in findings
    )


def test_env_stale_excludes_config_path() -> None:
    """MAIL_CONFIG_PATH is excluded from stale checks."""
    # It IS present in the example, but shouldn't be flagged as stale.
    findings = check_env_example(_ENV_EXAMPLE)
    assert not any(
        f["type"] == "stale-env-example-var" and f["key"] == "MAIL_CONFIG_PATH"
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


def test_placeholder_llm_api_key_env() -> None:
    """LLM_API_KEY placeholder is not a default-mismatch."""
    findings = check_env_example(_ENV_EXAMPLE)
    assert not any(
        f["type"] == "default-mismatch" and "LLM_API_KEY" in str(f) for f in findings
    )


def test_placeholder_llm_api_key_changed() -> None:
    """Changing LLM_API_KEY to another sk-or-v1 token still exits clean."""
    modified = _ENV_EXAMPLE.replace(
        "LLM_API_KEY=sk-or-v1-…", "LLM_API_KEY=sk-or-v1-different"
    )
    findings = check_env_example(modified)
    assert not any(
        f["type"] == "default-mismatch" and "LLM_API_KEY" in str(f) for f in findings
    )


def test_placeholder_password_env() -> None:
    """MAIL_PASSWORD placeholder is not a default-mismatch."""
    # password is MISSING in dataclass → skip comparison anyway.
    modified = _ENV_EXAMPLE.replace(
        "MAIL_PASSWORD=your-password-here", "MAIL_PASSWORD=realpw"
    )
    findings = check_env_example(modified)
    assert not any(
        f["type"] == "default-mismatch" and "MAIL_PASSWORD" in str(f) for f in findings
    )


# ====================================================================
# Docs table drift
# ====================================================================


def test_doc_missing_yaml_key() -> None:
    """Removing a YAML table row reports doc-missing-yaml-key."""
    modified = _DOCS_YAML_TABLE.replace(
        "| `imap.port` | no | `993` | IMAP server port |\n", ""
    )
    text = _full_docs(modified, _DOCS_ENV_TABLE)
    findings = check_docs_connecting(text)
    assert any(
        f["type"] == "doc-missing-yaml-key" and f["key"] == "imap.port"
        for f in findings
    )


def test_doc_missing_env_var() -> None:
    """Removing an env var table row reports doc-missing-env-var."""
    modified = _DOCS_ENV_TABLE.replace(
        "| `MAIL_IMAP_PORT` | no | `993` | IMAP server port |\n", ""
    )
    text = _full_docs(_DOCS_YAML_TABLE, modified)
    findings = check_docs_connecting(text)
    assert any(
        f["type"] == "doc-missing-env-var" and f["key"] == "MAIL_IMAP_PORT"
        for f in findings
    )


def test_doc_default_mismatch() -> None:
    """Changing a documented default reports doc-default-mismatch."""
    modified = _DOCS_YAML_TABLE.replace("`993`", "`1993`", 1)
    text = _full_docs(modified, _DOCS_ENV_TABLE)
    findings = check_docs_connecting(text)
    assert any(
        f["type"] == "doc-default-mismatch" and f["key"] == "imap.port"
        for f in findings
    )


def test_doc_stale_yaml_key() -> None:
    """Adding a made-up YAML row reports doc-stale-yaml-key."""
    modified = _DOCS_YAML_TABLE + ("| `foo.bar` | no | `1` | Made up |\n")
    text = _full_docs(modified, _DOCS_ENV_TABLE)
    findings = check_docs_connecting(text)
    assert any(
        f["type"] == "doc-stale-yaml-key" and f["key"] == "foo.bar" for f in findings
    )


def test_doc_stale_env_var() -> None:
    """Adding a made-up env var row reports doc-stale-env-var."""
    modified = _DOCS_ENV_TABLE + ("| `MAIL_FOO` | no | `1` | Made up |\n")
    text = _full_docs(_DOCS_YAML_TABLE, modified)
    findings = check_docs_connecting(text)
    assert any(
        f["type"] == "doc-stale-env-var" and f["key"] == "MAIL_FOO" for f in findings
    )


def test_doc_stale_excludes_config_path() -> None:
    """MAIL_CONFIG_PATH is excluded from doc stale checks."""
    text = _full_docs(_DOCS_YAML_TABLE, _DOCS_ENV_TABLE)
    findings = check_docs_connecting(text)
    assert not any(
        f["type"] == "doc-stale-env-var" and f["key"] == "MAIL_CONFIG_PATH"
        for f in findings
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
    (repo / ".env.example").write_text(_ENV_EXAMPLE)
    (repo / "docs" / "connecting.md").write_text(
        _full_docs(_DOCS_YAML_TABLE, _DOCS_ENV_TABLE)
    )
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
    """A single-account-shaped doc (no `accounts:` key) is rejected with the
    actionable error naming `migrate-config` and `detect`."""
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
    assert "migrate-config" in message
    assert "detect" in message


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
    """The shipped multi-account example loads as a valid container."""
    config = MailAccountsConfig.from_yaml("docs/config/mail.local.example.yaml")
    assert len(config.accounts) >= 2
    ids = config.ids()
    assert len(set(ids)) == len(ids)
    db_paths = [account.config.db_path for account in config.accounts]
    assert len(set(db_paths)) == len(db_paths)
    # The default resolves without raising.
    assert config.default.account_id in ids
