## Change Summary

<!-- Provide a one-sentence summary of what this PR changes and why. -->

## Description

<!--
Describe the motivation, context, and design decisions. Reference
any related issues (fixes #123). If this is a breaking change,
document the migration path.
-->

## Checklist

- [ ] **Changelog fragment**: I have added a changelog fragment file
      in `changelog/` with the naming convention
      `YYYYMMDDTHHMMSSZ-description-XXXX.<type>.md` (types: `feature`,
      `bugfix`, `removal`, `misc`), or this PR is labelled
      `Skip-Changelog`.
- [ ] **Tests added or updated**: New and existing test suites pass
      locally. For bug fixes, I added a test that reproduces the
      issue and fails before this change.
- [ ] **Type-checked**: `mypy .` passes with no new errors. Any
      `# type: ignore` comments include an inline justification.
- [ ] **Documentation updated**: If this changes public behaviour or
      adds a feature, I updated the relevant docs (in `docs/`)
      and/or added a docstring.
- [ ] **Pre-commit hooks**: All pre-commit hooks pass
      (`pre-commit run --all-files`).
- [ ] **Code registered**: Every new file in `src/` is registered in
      `docs/modules.yaml` under exactly one module's `paths` list.
