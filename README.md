# robotsix-auto-mail

A deliberately bare repository serving as a second, independent target for multi-repo testing of the [robotsix-mill](https://github.com/damien-robotsix/robotsix-mill) autonomous ticket solver.

## What it is

This repository is a **test fixture** — not a real application. It contains no source code, no build system, no CI pipeline, and no runtime. Its sole purpose is to act as an additional repository the mill can operate on, enabling validation of workflows that span multiple repos.

## Why it exists

The robotsix-mill is designed to solve tickets across more than one repository. A single-repo testbed can only exercise so much. This repo provides a second, independent surface for testing:

- Ticket propagation across repositories
- Multi-repo refactoring and coordinated changes
- Cross-repo branch management and PR lifecycles

## Repository contents

This repository contains only a `README.md` (and whatever else the mill produces when filing and solving tickets). There is no `src/`, no `package.json`, no `pyproject.toml`, no `Dockerfile` — nothing to install, run, or configure.

**Note on tests:** there is a single `test_placeholder.py` that contains one trivial assertion. It exists only so that `pytest` discovers at least one test and exits with code 0 rather than code 5 ("no tests collected"). There is no application code to test — the placeholder is a formality to keep the test runner happy.

## How it's managed

Every change in this repository is driven by tickets filed and resolved by the robotsix-mill. There is no manual development workflow, no human maintainers, and no release process. The history of this repo is a log of mill activity.

## Related

- [robotsix-mill](https://github.com/damien-robotsix/robotsix-mill) — the autonomous ticket solver that manages this repository
