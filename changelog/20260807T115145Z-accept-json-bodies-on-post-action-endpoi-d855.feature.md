POST action endpoints (`/move`, `/archive`, `/delete`, `/save-notes`, batch
operations, etc.) now accept JSON request bodies in addition to form-encoded
data, so clients sending ``Content-Type: application/json`` receive the same
behaviour as the board UI. Malformed JSON with a JSON content type returns a
clear 400 error.
Remove the spurious `changelog.d/*.md` glob from the `core` module's `paths` list in `docs/modules.yaml` — the `changelog.d/` directory does not exist; only `changelog/` does. This was a regression from a previous fix that was not properly persisted.
