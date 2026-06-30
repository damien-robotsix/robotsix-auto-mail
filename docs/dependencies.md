# Dependencies

robotsix-auto-mail consumes several first-party packages from git
(`robotsix-agent-comm`, `robotsix-board`, `robotsix-llmio`,
`robotsix-yaml-config`, `robotsix-modules`) alongside its PyPI
dependencies. `robotsix-agent-comm` backs the `calendar` / `broker`
extras. To keep builds reproducible and prevent silent breakage, we
follow a **pin + bump** model.

## The pin + bump model

- `uv.lock` is committed and is the **single source of truth** for the
  resolved dependency tree.
- Most git sources in `[tool.uv.sources]` stay at `rev = "main"`, but the
  lock pins the **exact resolved commit** for each one, e.g.
  `robotsix-board.git?rev=main#<sha>`. One source is the exception:
  `robotsix-llmio` is pinned directly in `pyproject.toml` to a specific
  commit (`rev = "28b23a848003"`) rather than tracking `main`.
- Nothing installs `@main` at build/run time. Every install path honors
  the committed lock, so the resolved revisions only move when `uv.lock`
  is updated and committed.

## How installs honor the lock

- **CI** (`.github/workflows/ci.yml`, both the `verify` and `security`
  jobs) installs via `uv sync --frozen --extra dev`. `--frozen` uses
  `uv.lock` as-is and **fails** if `pyproject.toml` drifted from the
  lock — guaranteeing the committed lock is authoritative.
- **The Docker build** (`Dockerfile`, builder stage) installs the
  exported frozen lock rather than re-resolving `@main`:

  ```dockerfile
  RUN uv export --frozen --no-emit-project --no-hashes --extra llm --extra microsoft --extra calendar -o /tmp/requirements.txt && \
      uv pip install --system --no-cache-dir -r /tmp/requirements.txt && \
      uv pip install --system --no-cache-dir --no-deps .
  ```

  `uv export --frozen` reads `uv.lock` as-is (no re-resolution) and emits
  the pinned git URLs (`...?rev=main#<sha>`); `--no-emit-project` drops
  the local project from the requirements file so it is installed
  separately with `--no-deps` (its deps are not re-resolved);
  `--no-hashes` avoids the hash/VCS conflict (uv cannot hash a git
  checkout). The result: two builds with no repo change install
  identical revisions — no `@main` drift.

  > Plain `uv pip install ".[llm]"` performs a **fresh resolution** and
  > does **not** read `uv.lock`, so it must not be used in the build.

## How updates land

Dependency-revision movement arrives through one CI-gated path, never via
silent `@main` drift:

- **Manifest change** (`.github/workflows/lockfile.yml`): when
  `pyproject.toml` changes on `main`, the lockfile is re-resolved and
  committed back so the lock never goes stale.

## Motivating incident — 2026-06-10 board outage

A production outage on 2026-06-10 was caused by an auto-mail container
build pulling shared git dependencies **unpinned at `@main`** at
image-build time. `robotsix-board` #40 changed the `BoardAdapter`
Protocol; the 13:15 auto-mail rebuild silently pulled it, and the
container crash-looped on the import-time Protocol assert
(`MailBoardAdapter` does not satisfy `BoardAdapter`) until an operator
hotfix (board #41) plus a manual `--no-cache` rebuild.

A committed lock plus CI-gated bumps would have prevented this:

- The Docker build installs the **frozen lock**, so the rebuild would
  have used the previously-pinned `robotsix-board` commit — not whatever
  `@main` pointed to at 13:15. No silent pull, no crash loop.
- The board #40 change would instead reach auto-mail through a deliberate
  lock update (re-resolved and committed via `lockfile.yml`), whose CI
  runs the full suite — including
  `tests/server/test_board_adapter.py::test_mailboardadapter_satisfies_protocol`,
  which asserts `MailBoardAdapter` satisfies the `BoardAdapter` Protocol.
  The Protocol break would have failed CI and been caught before merge,
  never reaching production.
