POST action endpoints (`/move`, `/archive`, `/delete`, `/save-notes`, batch
operations, etc.) now accept JSON request bodies in addition to form-encoded
data, so clients sending ``Content-Type: application/json`` receive the same
behaviour as the board UI. Malformed JSON with a JSON content type returns a
clear 400 error.
