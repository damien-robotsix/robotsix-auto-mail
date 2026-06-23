#!/usr/bin/env bash
# Thin wrapper around the shared `robotsix-autoupdate` CLI. All the
# autoupdate logic (flock, branch pinning, fetch/SHA-compare, ff-merge,
# docker compose build/up, deployed-SHA recording) lives in the Python
# module so it stays in sync with robotsix-mill. Auto-mail has no
# idle-check API, so `--no-idle-check` is always passed.
#
# Runtime files (log, deployed-SHA marker, lock) are written to the
# repo's PARENT directory so they never dirty the working tree.
#
# Install (cron, every 30 min):
#   15,45 * * * * /path/to/robotsix-auto-mail/scripts/dev/auto-mail-autoupdate.sh
set -uo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Self-locating: this script lives in scripts/dev/, so REPO is two levels up.
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)" || exit 1
REPO="$(dirname "$(dirname "$SCRIPT_DIR")")"
STATE_DIR="$(dirname "$REPO")"          # runtime files live outside the repo

exec "$REPO/.venv/bin/robotsix-autoupdate" \
  --repo "$REPO" \
  --state-dir "$STATE_DIR" \
  --state-prefix auto-mail-autoupdate \
  --service robotsix-auto-mail \
  --ensure-branch main \
  --no-idle-check
