#!/usr/bin/env bash
#
# Runtime-verification smoke test for the robotsix-auto-mail board server.
#
# Boots the board HTTP server in full isolation against a throwaway SQLite
# database, curls the key routes, asserts HTTP 200 plus DOM/JSON markers, and
# shuts down cleanly. Talks only to 127.0.0.1 and never connects to IMAP/SMTP
# at boot (dummy host values are sufficient). Exits non-zero with a distilled
# diagnosis (failing route + status + a tail of the server log) on any failure.
#
# Run from the repo root:
#   bash scripts/server/smoke_board.sh
#
set -euo pipefail

PORT="${SMOKE_PORT:-8099}"
HOST="127.0.0.1"
BASE="http://${HOST}:${PORT}"

TMP_DIR="$(mktemp -d)"
LOG_FILE="${TMP_DIR}/server.log"
SERVER_PID=""

# Invoked indirectly via `trap cleanup EXIT`, so shellcheck's reachability
# analysis cannot see the call site (SC2317 on the whole body).
# shellcheck disable=SC2317
cleanup() {
    if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
        kill "${SERVER_PID}" 2>/dev/null || true
        wait "${SERVER_PID}" 2>/dev/null || true
    fi
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

diagnose() {
    # $1: route, $2: status (or message), $3: extra detail
    echo "SMOKE FAILURE: ${1} -> ${2}" >&2
    if [[ -n "${3:-}" ]]; then
        echo "  ${3}" >&2
    fi
    echo "--- server log (tail) ---" >&2
    tail -n 40 "${LOG_FILE}" >&2 2>/dev/null || echo "  (no server log captured)" >&2
    echo "-------------------------" >&2
}

# Isolated, dependency-free configuration: write a minimal multi-account
# config.yaml to the temp dir and point MAIL_CONFIG_PATH at it. Dummy
# IMAP/SMTP values are fine because no mail connection is made at boot.
CONFIG_FILE="${TMP_DIR}/config.yaml"
cat >"${CONFIG_FILE}" <<EOF
default_account: smoke
accounts:
  - id: smoke
    label: Smoke test
    imap:
      host: localhost
    smtp:
      host: localhost
    auth:
      username: smoke
      password: smoke  # pragma: allowlist secret
    store:
      path: ${TMP_DIR}/smoke.db
EOF
export MAIL_CONFIG_PATH="${CONFIG_FILE}"

# Launch the server in the background, capturing stdout/stderr for diagnosis.
# Prefer `uv run --frozen` when available; the mill test-gate sandbox has no
# `uv` binary, so fall back to launching via the installed package layout with
# PYTHONPATH pointing at the repo source and relying on the sandbox-installed
# runtime packages for dependencies.
if command -v uv >/dev/null 2>&1; then
    uv run --frozen robotsix-auto-mail serve --port "${PORT}" >"${LOG_FILE}" 2>&1 &
else
    PYTHONPATH="src" python -c 'import sys; from robotsix_auto_mail.cli import main; sys.exit(main())' serve --port "${PORT}" >"${LOG_FILE}" 2>&1 &
fi
SERVER_PID=$!

# Readiness poll: wait until /board answers 200, with a bounded timeout.
ready=0
for _ in {1..40}; do
    if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
        diagnose "boot" "server process exited before becoming ready"
        exit 1
    fi
    if curl -fsS -o /dev/null "${BASE}/board" 2>/dev/null; then
        ready=1
        break
    fi
    sleep 0.5
done

if [[ "${ready}" -ne 1 ]]; then
    diagnose "GET /board" "not ready within timeout"
    exit 1
fi

# Helper: fetch a route into temp files. Writes the response body to
# ${BODY_FILE} and the HTTP status code to ${STATUS_FILE}. Both are read
# back by the caller in the parent shell — the status is NOT returned via a
# variable, because `fetch` is invoked outside a command substitution so no
# subshell can swallow the assignment.
BODY_FILE="${TMP_DIR}/body.out"
STATUS_FILE="${TMP_DIR}/status.out"

fetch() {
    local path="$1"
    curl -s -o "${BODY_FILE}" -w '%{http_code}' "${BASE}${path}" >"${STATUS_FILE}"
}

# --- Assertion 1: GET /board -> 200 + DOM markers ---
fetch /board
status="$(<"${STATUS_FILE}")"
body="$(<"${BODY_FILE}")"
if [[ "${status}" != "200" ]]; then
    diagnose "GET /board" "${status}" "expected HTTP 200"
    exit 1
fi
if [[ "${body}" != *"<title>Mail Board</title>"* ]]; then
    diagnose "GET /board" "${status}" "missing marker: <title>Mail Board</title>"
    exit 1
fi
if [[ "${body}" != *'class="board"'* ]]; then
    diagnose "GET /board" "${status}" 'missing marker: class="board"'
    exit 1
fi

# --- Assertion 2: GET /board-content -> 200 + JSON key columns_html ---
fetch /board-content
status="$(<"${STATUS_FILE}")"
body="$(<"${BODY_FILE}")"
if [[ "${status}" != "200" ]]; then
    diagnose "GET /board-content" "${status}" "expected HTTP 200"
    exit 1
fi
if [[ "${body}" != *'"columns_html"'* ]]; then
    diagnose "GET /board-content" "${status}" 'missing JSON key: columns_html'
    exit 1
fi

# --- Assertion 3: GET /static/board.css -> 200 (robotsix_board assets) ---
fetch /static/board.css
status="$(<"${STATUS_FILE}")"
if [[ "${status}" != "200" ]]; then
    diagnose "GET /static/board.css" "${status}" "expected HTTP 200 (robotsix_board static assets)"
    exit 1
fi

echo "smoke OK: /board, /board-content, /static/board.css all rendered (port ${PORT})"
exit 0
