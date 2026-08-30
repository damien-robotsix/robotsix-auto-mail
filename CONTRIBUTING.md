# Contributing to robotsix-auto-mail

Contributions are welcome — whether it's a bug report, a feature request,
a documentation fix, or a code change. This document is a short gateway;
the full development guide lives at [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)
and covers setup, testing, code style, and the pull request process in detail.

## Code of Conduct

This project is governed by a [Code of Conduct](.github/CODE_OF_CONDUCT.md).
By participating, you agree to uphold its terms. Report unacceptable behavior
to damien.robotsix@gmail.com.

## AI/LLM contribution policy

AI-assisted contributions are welcome, but the author must fully understand
and take responsibility for every line of code. LLM-generated PRs submitted
without meaningful human review will be closed. See
[standards.robotsix.net/ai-contributions](https://standards.robotsix.net/ai-contributions)
for the full policy.

## Release procedure

Commit subjects and PR titles must be conventional
(`feat:`/`fix:`/`chore:`/`docs:`/`refactor:`/`test:`/`ci:`).
release-please generates `CHANGELOG.md` automatically from these
conventional commits — no manual changelog steps are needed.

1. Tag and push:

   ```bash
   git tag vX.Y.Z && git push --tags
   ```
