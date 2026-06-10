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
#   bash scripts/smoke_board.sh
#
set -euo pipefail

PORT="${SMOKE_PORT:-8099}"
HOST="127.0.0.1"
BASE="http://${HOST}:${PORT}"

TMP_DIR="$(mktemp -d)"
LOG_FILE="${TMP_DIR}/server.log"
SERVER_PID=""

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

# Isolated, dependency-free configuration: MailConfig.from_env() succeeds as
# soon as the four required vars are present. Dummy IMAP/SMTP values are fine
# because no mail connection is made at boot.
export MAIL_IMAP_HOST="localhost"
export MAIL_SMTP_HOST="localhost"
export MAIL_USERNAME="smoke"
export MAIL_PASSWORD="smoke"
export MAIL_DB_PATH="${TMP_DIR}/smoke.db"

# Launch the server in the background, capturing stdout/stderr for diagnosis.
uv run --frozen robotsix-auto-mail serve --port "${PORT}" >"${LOG_FILE}" 2>&1 &
SERVER_PID=$!

# Readiness poll: wait until /board answers 200, with a bounded timeout.
ready=0
for _ in $(seq 1 40); do
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

# Helper: fetch a route, return body on stdout, set REPLY_STATUS to HTTP code.
fetch() {
    local path="$1"
    local body_file="${TMP_DIR}/body.out"
    REPLY_STATUS="$(curl -s -o "${body_file}" -w '%{http_code}' "${BASE}${path}")"
    cat "${body_file}"
}

# --- Assertion 1: GET /board -> 200 + DOM markers ---
body="$(fetch /board)"
if [[ "${REPLY_STATUS}" != "200" ]]; then
    diagnose "GET /board" "${REPLY_STATUS}" "expected HTTP 200"
    exit 1
fi
if [[ "${body}" != *"<title>Mail Board</title>"* ]]; then
    diagnose "GET /board" "${REPLY_STATUS}" "missing marker: <title>Mail Board</title>"
    exit 1
fi
if [[ "${body}" != *'class="board"'* ]]; then
    diagnose "GET /board" "${REPLY_STATUS}" 'missing marker: class="board"'
    exit 1
fi

# --- Assertion 2: GET /board-content -> 200 + JSON key columns_html ---
body="$(fetch /board-content)"
if [[ "${REPLY_STATUS}" != "200" ]]; then
    diagnose "GET /board-content" "${REPLY_STATUS}" "expected HTTP 200"
    exit 1
fi
if [[ "${body}" != *'"columns_html"'* ]]; then
    diagnose "GET /board-content" "${REPLY_STATUS}" 'missing JSON key: columns_html'
    exit 1
fi

# --- Assertion 3: GET /static/board.css -> 200 (robotsix_board assets) ---
fetch /static/board.css >/dev/null
if [[ "${REPLY_STATUS}" != "200" ]]; then
    diagnose "GET /static/board.css" "${REPLY_STATUS}" "expected HTTP 200 (robotsix_board static assets)"
    exit 1
fi

echo "smoke OK: /board, /board-content, /static/board.css all rendered (port ${PORT})"
exit 0
