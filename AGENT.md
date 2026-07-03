# AGENT.md — hard rules for agents operating on robotsix-auto-mail

This file encodes conventions that, when violated, cause recurring breakage
in CI, test gates, or the fleet's periodic workflows.  It is *not* a general
orientation document; it is a set of constraints the agent system must
follow.

This repo follows the [robotsix stack standards](https://github.com/damien-robotsix/robotsix-standards).

---

## Testing conventions

### The test gate is sacred

**Tests never touch the network and never consume LLM tokens.**  The
`conftest.py` fixture `_block_network` enforces this by replacing
`socket.create_connection` with a raising stub for every non-`integration`
test.  Do **not** weaken or remove this fixture.

### Mock seams for LLM agents

Every LLM call site in the codebase routes through two external-library
entry points — these are the **only** surfaces you mock in tests:

- **`robotsix_llmio.core.get_provider_for_identifier`** — returns a
  provider whose `build_agent()` produces the agent handle.
- **`robotsix_llmio.core.run_agent`** — runs the agent handle and
  returns the validated output.

Patch **`get_provider_for_identifier`**, NOT `_run_llm_agent` or
`build_agent` directly.  The shared helper `_run_llm_agent` performs
lazy imports inside its body precisely so test patches can intercept at
these two seams.

Example pattern (see `tests/triage/test_triage_agent.py`):
```python
mock_provider = mock.MagicMock()
mock_handle = mock.MagicMock()
mock_handle.run_sync.return_value = mock.MagicMock(output=result_obj)
mock_provider.build_agent.return_value = mock_handle
with mock.patch(
    "robotsix_llmio.core.get_provider_for_identifier",
    return_value=mock_provider,
):
    ...
```

### IMAP / SMTP mock factories

Use the shared mock factories in `tests/conftest.py` instead of
hand-rolling `imaplib.IMAP4_SSL` / `smtplib.SMTP` mocks:

- `_make_mock_imap_ssl()` — returns `MagicMock(spec=imaplib.IMAP4_SSL)`
- `_make_mock_imap()` — returns `MagicMock(spec=imaplib.IMAP4)`
- `_make_mock_smtp_ssl()` — returns `MagicMock(spec=smtplib.SMTP_SSL)`
- `_make_mock_smtp()` — returns `MagicMock(spec=smtplib.SMTP)`

### Test file size

When a test file exceeds ~500 lines with clear thematic sections
(separated by `# -----` or `# =====` comment blocks), split it into
domain-focused modules under the same directory — one module per
endpoint, handler mixin, or logical concern.

---

## Configuration conventions

### The `mail.local.example.yaml` / `MailConfig` pair

Configuration is loaded from a single YAML config file only — there are no
environment-variable overrides (with two narrow exceptions: ``LLM_API_KEY``
and ``LLM_PROVIDER_MODEL`` are read as env-var fallbacks for LLM key/model
resolution, via ``resolve_llm_api_key`` and ``resolve_llm_provider_model`` in
``config/loader.py``). Every configuration field lives on the
`MailConfig` frozen dataclass (`src/robotsix_auto_mail/config/model.py`).
When you **add** a new configuration field you MUST update **both** artifacts:

1. **`MailConfig`** — add the dataclass field with its default.
2. **`docs/config/mail.local.example.yaml`** — add the commented-out entry so
   users know it exists.

The `_FIELD_SPECS` table in `src/robotsix_auto_mail/config/schema.py`
must enumerate every `MailConfig` field exactly once — an
`assert _spec_names == _dc_names` guard at import time enforces this.
When you add a field, add its `_FieldSpec` row in the same commit.

Failure mode: if the two artifacts drift, the `config-sync` CLI
subcommand reports the gap, and CI gates on it.

### secrets

Credentials are masked in `MailConfig.__repr__` via `_SECRET_FIELDS`.
Add any new secret field to that tuple.  Never log or repr a raw
credential.

### Multi-account config shape

The YAML config uses the `accounts:` list shape — the only supported
config-file shape. New features MUST work in multi-account mode.

---

## Repo-specific gotchas

### Board adapter — structural Protocol compliance

`src/robotsix_auto_mail/server/board_adapter.py` defines
`MailBoardAdapter`, which must satisfy the `BoardAdapter` Protocol from
`robotsix_board`.  The compliance check runs at **import time**
(`_verify_protocol()` at module bottom) via `isinstance(adapter,
BoardAdapter)`.

When you add, remove, or rename a `BoardAdapter` protocol method in
`robotsix-board`, the auto-mail adapter must be updated in lockstep —
otherwise auto-mail's import fails at startup, breaking the web board.

The duck-typed raw-HTML hooks (`card_extra_html`, `column_extra_html`)
are deliberately omitted from the runtime-checkable Protocol (they are
looked up via `getattr` by `robotsix_board.render_board()`), so adding
or removing them does **not** affect the `isinstance` check.

### `_migrate.py` — promotable-verbatim intent

`src/robotsix_auto_mail/db/_migrate.py` is designed for eventual
extraction into a fleet-shared library.  Every new migration MUST:

- Be **backend-agnostic in shape** — rely only on `conn.execute(...)` +
  `conn.commit()` and on catching `sqlite3.OperationalError` for the
  "duplicate column" case.  Do NOT import or couple to SQLAlchemy /
  SQLModel.
- Be **idempotent** — running it multiple times must be a safe no-op.
- Be **additive** — never drop columns, rename tables, or otherwise
  destroy data.  Add columns, rebuild CHECK constraints when the
  vocabulary grows (see `_migrate_triage_action_check`), or remap
  legacy values.

The `conn` parameter is typed as `sqlite3.Connection` because auto-mail
only ever passes a raw connection; this is a deliberate choice to keep
the helpers promotable into codebases that use different ORMs.

### Static assets — no inline CSS/JS

Static assets (CSS, JS) live in `src/robotsix_auto_mail/server/static/` and
are loaded at module level via `Path(__file__).parent / "static" /
"<filename>").read_text()`.  Do **not** embed CSS or JS as Python
string literals in `server/` — the separation keeps the server module
navigable and allows CSS/JS tooling to apply.

---

## Logging / Tracing

### Delegate, never re-implement

All structured-logging and Langfuse-tracing infrastructure lives in
`robotsix_llmio`.  auto-mail delegates to it:

- **Stream handler + formatter + OTel trace-id injection**: via
  `robotsix_llmio.logging.setup_logging()` (called from
  `src/robotsix_auto_mail/observability/__init__.py`).
- **Langfuse tracing**: `robotsix_llmio.core.run_agent` automatically
  traces every LLM call when Langfuse credentials are configured (via the
  config file's `langfuse:` section).  No extra code needed in auto-mail.

All application logs go to stdout/stderr at the configured level
(via ``robotsix_llmio.logging.setup_logging``).
Never add another tracing framework, log shipper, or OTel exporter
directly — extend `robotsix_llmio` instead.

### Langfuse config

Langfuse credentials live on `MailConfig` (`langfuse_public_key`,
`langfuse_secret_key`, `langfuse_base_url`) and are populated from
`config/mail.local.yaml` (under the top-level `langfuse:` section).
They are application-wide (not per-account).

---

## CI / Workflow invariants

auto-mail has 8 CI workflows (plus periodic fleet-driven workflows that
target it).  Every workflow that produces artifacts or reports MUST
upload them as workflow artifacts — the periodic fleet jobs consume
them.  Do NOT remove an `upload-artifact` step without understanding
which downstream consumer needs it.

The `pre-commit.yml` workflow runs `ruff`, `mypy`, `vulture`, and
`robotsix-modules check-registration`.  Any new source or test file
MUST be registered in `docs/modules.yaml` under the appropriate module,
or the pre-commit gate will fail.

### Changelog fragments (towncrier)

Every PR that adds or modifies source, test, or configuration files
MUST include a changelog fragment file under `changelog/`.  The fragment
must be a ``.md`` file whose name follows the towncrier convention
(``<ID>.<type>.md``) and whose type matches one of the configured
categories: ``feature``, ``bugfix``, ``removal``, or ``misc``.  The
`towncrier check` CI gate in `ci.yml` verifies this on pull requests.

---

## Documentation conventions

When you add or change a user-facing CLI subcommand in
`src/robotsix_auto_mail/cli/__init__.py`, document it in `docs/connecting.md` in
the same PR, following the `config-sync` command section pattern
(purpose, optional-extra requirements, flags, example invocation, and
output).
