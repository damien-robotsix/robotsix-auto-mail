# CodeQL Verification

This page is the authoritative, in-repo procedure for how CodeQL alerts
are verified and suppressed in `robotsix-auto-mail`, and why the
code-scanning **alerts API** is currently unavailable here. It exists so
that the periodic `maintenance` agent (and human operators) have
deterministic guidance instead of an open-ended "cannot verify CodeQL"
report.

## Current state: why the alerts API is unavailable

GitHub **code scanning is not enabled** in this repository's settings.
As a result, the code-scanning *alerts* API —

```sh
gh api repos/<owner>/<repo>/code-scanning/alerts
```

— returns a configuration error and has no data to return for this repo.

The source of truth for this is
[`.github/workflows/codeql.yml`](https://github.com/damien-robotsix/robotsix-auto-mail/blob/main/.github/workflows/codeql.yml).
Its `Perform CodeQL Analysis` step is marked `continue-on-error: true`
with an inline comment that paraphrases as: code scanning is not yet
enabled in this repository's settings, so the SARIF upload returns a
configuration error; the analysis still runs (it is cheap and validates
the workflow) but CI does not fail on the upload rejection, and once an
operator enables code scanning the upload succeeds and the workaround can
be removed.

Separately, the `maintenance` agent's sandbox has **no `gh` CLI and no
network access**, so it cannot query the code-scanning API regardless of
whether scanning is enabled. The agent's toolset and sandbox are defined
upstream in the `robotsix-mill` framework, not in this repository — see
[Operator / framework steps](#operator--framework-steps-out-of-repo)
below.

## How CodeQL runs here

[`.github/workflows/codeql.yml`](https://github.com/damien-robotsix/robotsix-auto-mail/blob/main/.github/workflows/codeql.yml)
analyzes the `python` language with the **`security-and-quality`** query
suite. It runs on:

- every `push` (all branches),
- `pull_request` (opened / synchronize), and
- a weekly `schedule` (Monday baseline-drift scan).

Before initializing CodeQL, the workflow installs the full dependency
graph with `uv sync --frozen --extra dev` and points the extractor at the
uv-managed interpreter (`CODEQL_PYTHON`). This lets the Python extractor
resolve third-party imports. Because Python is interpreted, there is no
build/autobuild step.

[`.github/codeql/codeql-config.yml`](https://github.com/damien-robotsix/robotsix-auto-mail/blob/main/.github/codeql/codeql-config.yml)
applies `paths-ignore: [tests]`, excluding test code from analysis. Tests
deliberately contain patterns the suite flags, so the real security
surface analyzed strictly is `src/`.

## The sanctioned suppression convention

Intentional findings and false positives are suppressed **narrowly, per
line**, with an inline `# lgtm[<rule-id>]` comment and a short
explanatory comment on the line(s) above the flagged statement. This is
the only sanctioned mechanism for handling individual findings.

Live examples in
[`src/robotsix_auto_mail/imap/client.py`](https://github.com/damien-robotsix/robotsix-auto-mail/blob/main/src/robotsix_auto_mail/imap/client.py)
(and [`src/robotsix_auto_mail/imap/_protocol.py`](https://github.com/damien-robotsix/robotsix-auto-mail/blob/main/src/robotsix_auto_mail/imap/_protocol.py)):

- `# lgtm[py/empty-except]` — on the intentionally-empty `except` handler
  in `_close_socket` (alongside `# noqa: S110  # nosec B110`).
- `# lgtm[py/clear-text-transmission-sensitive-data]` — on the plaintext
  socket opened only to negotiate a STARTTLS upgrade (the
  operator-selected `tls_mode == "starttls"` path), and on the explicit
  `tls_mode == "none"` plaintext path.
- `# lgtm[py/clear-text-storage-sensitive-data]` — on credential-bearing
  lines in the authentication path (in `client.py`, and in
  `_protocol.py`'s `build_xoauth2_response`).

The rule:

- Use a narrowly-scoped per-line `# lgtm[<rule-id>]` for each
  intentional / false-positive finding, with an explanatory comment
  stating why it is safe.
- Do **not** broaden the `paths-ignore` list in
  `.github/codeql/codeql-config.yml` to silence findings across whole
  modules. `paths-ignore` is reserved for test code only.

## Verification procedure for the maintenance agent

This procedure does **not** depend on the code-scanning alerts API and is
fully achievable in the agent's sandbox:

1. **Audit the existing suppressions.** Treat the `# lgtm[<rule-id>]`
   comments under `src/` as the audit surface (for example,
   `grep -rn "lgtm\[" src/`). Each one must:
   - keep its short explanatory comment describing why the finding is
     safe, and
   - stay attached to the exact statement it suppresses (inline, or on
     the line immediately above it).
2. **Confirm the workflow still runs.** Verify that the CodeQL workflow
   defined in `.github/workflows/codeql.yml` is intact and still runs
   green — or green-via-`continue-on-error` on the upload step — in CI.
3. **Cross-check via the API only when it becomes available.** Once code
   scanning is enabled and `gh`/network access is granted (see below),
   cross-check rule IDs and line numbers against
   `gh api repos/<owner>/<repo>/code-scanning/alerts`. Until then, this
   step is not possible and must not be treated as a blocker.

If all in-repo checks pass, CodeQL handling is verified for this repo;
the absence of API access is an environmental constraint, not a defect.

## Operator / framework steps (out of repo)

The following are the genuine unblock for **API-based** verification.
Neither can be performed from within this repository:

- **(a) An operator enables code scanning** in the repository's GitHub
  settings (Security → Code scanning). After this, the SARIF upload in
  `codeql.yml` succeeds and the `continue-on-error: true` workaround on
  the `Perform CodeQL Analysis` step can be removed.
- **(b) The upstream `robotsix-mill` framework grants the `maintenance`
  agent `gh` CLI / network access** so it can query the code-scanning
  alerts API at all.

Until both happen, follow the in-repo verification procedure above.
