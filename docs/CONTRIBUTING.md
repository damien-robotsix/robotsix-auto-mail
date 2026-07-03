# Contributing

Thanks for your interest in improving `robotsix-auto-mail`. This guide
covers how to set up a development environment, run the tests, meet the
code-style expectations, and open a pull request.

## Development environment

This project uses a `src` layout and targets the Python version pinned in
`.python-version`. After cloning, install the package with its dev
extras:

```sh
uv sync --extra dev
# or, with pip:
pip install -e '.[dev]'
```

The dev extras pull in `pytest`, `mypy`, `ruff`, and `deptry` — the tools
the quality gate relies on.

This repository uses [pre-commit](https://pre-commit.com) to lint and
format code before each commit. After installing the dev extras, enable
the hooks once:

```sh
pip install pre-commit && pre-commit install
```

## Running tests

Run the suite from the repository root:

```sh
pytest
```

The full test layout, mocking strategy, and coverage expectations are
documented in [docs/testing.md](testing.md).

## Building documentation

Project documentation uses [MkDocs](https://www.mkdocs.org/) with the Material theme. To build and serve docs locally:

```sh
uv sync --frozen --extra docs
uv run --frozen mkdocs serve
```

Then open http://localhost:8000 in your browser. The site will auto-reload as you edit markdown files in the `docs/` directory.

To build a static copy of the site (e.g., before committing documentation changes):

```sh
uv run --frozen mkdocs build
```

This generates a `site/` directory with the built HTML (which is not committed to the repository).

## Code style and quality gate

CI (`.github/workflows/ci.yml`) is the source of truth for the quality
gate. Its `ci` job delegates lint/type/dependency/test checks to the shared
`robotsix-github-workflows` reusable workflow, and the `repo-checks`,
`security`, and `dependency-review` jobs add further checks on top. The
commands below mirror the core of that gate locally — useful before pushing,
though CI runs more:

```sh
ruff check .          # lint
mypy .                # type check (strict mode)
deptry src/ tests/    # dependency hygiene
pytest                # tests
python scripts/config/check_config_sync.py   # config-surface drift
```

A few conventions worth knowing up front:

- New code ships with tests in the matching `tests/<module>/` subdirectory.
- Mypy runs in strict mode over the whole repo, tests included, so every
  function (and test) is fully type-annotated.
- Every new repo file must be registered in
  [docs/modules.yaml](modules.yaml) under exactly one module's `paths`
  list, or the module-classification drift check will fail CI.

## Pull request process

1. Create a branch for your change.
2. Make focused commits; keep unrelated changes out of the same PR.
3. Run the quality gate above and make sure it passes locally.
4. Open a pull request describing what changed and why. Reference any
   related issue. When you open a PR, GitHub will pre-fill the
   description with a checklist from `.github/PULL_REQUEST_TEMPLATE.md`.
   Tick off each item before requesting review.
5. A maintainer will review your PR. Address review feedback by pushing
   follow-up commits to the same branch.

## Reporting issues and requesting features

Open an issue on the project's tracker. For bug reports, include steps to
reproduce, the expected and actual behaviour, and relevant configuration
or log output (with secrets redacted). For feature requests, describe the
use case and the outcome you want.

## Code of Conduct

This project is governed by a [Code of Conduct](../.github/CODE_OF_CONDUCT.md).
By participating, you agree to uphold its terms. Report unacceptable behavior
to damien.robotsix@gmail.com.

## AI/LLM contribution policy

AI-assisted contributions are welcome, but the author must fully understand
and take responsibility for every line of code. LLM-generated PRs submitted
without meaningful human review will be closed. See
[standards.robotsix.net/ai-contributions](https://standards.robotsix.net/ai-contributions)
for the full policy.
