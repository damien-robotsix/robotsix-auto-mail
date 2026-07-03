# Contributing to robotsix-auto-mail

## Release procedure

1. Collect fragment files and build the changelog entry:

   ```bash
   uv run --with towncrier towncrier build --yes --version X.Y.Z
   ```

   This reads all fragment files under `changelog/`, appends a new release
   section to `CHANGELOG.md`, and deletes the consumed fragments.

2. Commit the updated `CHANGELOG.md` (fragments are deleted automatically
   by towncrier):

   ```bash
   git add CHANGELOG.md changelog/
   git commit -m "Release vX.Y.Z"
   ```

3. Tag and push:

   ```bash
   git tag vX.Y.Z && git push --tags
   ```
