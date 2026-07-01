# Testing

How to run the test suite, how tests are organized, and what is expected of
new code.  The architecture these tests cover is described in
[docs/architecture.md](architecture.md).

## Running tests

Install the dev extras first (this pulls in `pytest`, `mypy`, and `ruff`):

```sh
uv sync --extra dev
# or, with pip:
pip install -e '.[dev]'
```

Then run the suite:

```sh
pytest
```

Pytest is configured in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

`testpaths` scopes collection to `tests/`, and `pythonpath = ["src"]` makes
the `src`-layout package importable without an editable install.

## Full local quality gate

CI (`.github/workflows/ci.yml`) is the source of truth for the gate.  It is
made of four jobs:

- **`ci`** — delegates to the reusable
  `robotsix-github-workflows/.github/workflows/python-ci.yml` workflow, which
  is where ruff, mypy, deptry, and pytest+coverage actually run.  It is
  pinned to Python 3.14 (`requires-python = ">=3.14,<3.15"` in
  `pyproject.toml` — a single version, no matrix).
- **`repo-checks`** — auto-mail-specific steps with no equivalent in the
  reusable spine: the config-sync drift check
  (`python scripts/config/check_config_sync.py`), Dockerfile lint, module
  taxonomy/registration validation, changelog enforcement, vulture dead-code
  detection, `validate-pyproject`, and the zizmor workflow scan.
- **`security`** — delegates to the reusable
  `python-security.yml` workflow (secret scan, pip-audit).
- **`sbom`** — generates a standalone CycloneDX SBOM of the full Python
  dependency tree (direct + transitive) from `uv.lock` and uploads it as a
  downloadable workflow artifact.
- **`dependency-review`** — diff-scoped dependency review on pull requests.

To mirror the `ci` job locally before pushing, run:

```sh
ruff check .          # lint
mypy .                # type check (strict mode)
deptry src/ tests/    # dependency hygiene
pytest                # tests
python scripts/config/check_config_sync.py   # config-surface drift
```

Mypy runs in strict mode (`[tool.mypy] strict = true` in `pyproject.toml`),
so the whole repo must type-check cleanly.  Running `ruff check .`, `mypy .`,
and `pytest` locally before pushing mirrors what CI enforces.

## Test organization

Tests live under `tests/` with per-module subdirectories that mirror `src/`
(`tests/imap/`, `tests/smtp/`, `tests/cli/`, `tests/db/`, `tests/fetch/`,
`tests/parser/`, `tests/pipeline/`, `tests/detect/`, `tests/archive/`,
`tests/status/`, `tests/triage/`, `tests/config/`, `tests/server/`, …).  The
`tests/` package itself has an `__init__.py`, and some subdirectories carry
their own (e.g. `tests/component_agent/__init__.py`).  Top-level files such as
`tests/test_stub.py` and the shared `tests/conftest.py` are also valid.

Conventions every test file follows:

- It starts with `from __future__ import annotations`.
- It has a module docstring, and each test function has a one-line docstring.
- Every test function (and every override/helper) is annotated `-> None`,
  because mypy strict is enforced over the whole repo — tests included.
- When a test file exceeds ~500 lines and has clear thematic sections
  (separated by `# -----` or `# =====` comment blocks), split it into
  domain-focused modules under the same directory — one module per endpoint,
  handler mixin, or logical concern.

## Mocking strategy

The protocol clients are unit-tested without a live server:

- IMAP tests patch the stdlib `imaplib` entry points; SMTP tests patch the
  stdlib `smtplib` entry points.  Connection, TLS, and auth failures are
  exercised with error-injection patterns under `tests/imap/` and
  `tests/smtp/` (raising the relevant `imaplib`/`smtplib`/`ssl` errors so the
  client maps them to its own exception types).
- The abstract `_ProtocolClient` base is tested through a local concrete
  test-double subclass rather than the real IMAP/SMTP clients.
- Deliberately bad-type calls to `str`-typed parameters carry a
  `# type: ignore[arg-type]` so the strict mypy gate stays green.
- CLI refactor tests patch `robotsix_auto_mail.cli._verify_config` and
  `getpass.getpass` at the module path.

## Coverage expectations

The project convention is that every `src/` module has a matching
`tests/<module>/` suite, and new code ships with tests in the corresponding
subdirectory.  Coverage is enforced in CI via `pytest-cov` with a
repo-wide threshold of **80%** (`[tool.coverage.report] fail_under = 80` in
`pyproject.toml`).  The expectation is still qualitative: cover the new
behaviour (including its failure paths) — the numeric gate exists to prevent
silent regressions, not to encourage coverage-driven development.
