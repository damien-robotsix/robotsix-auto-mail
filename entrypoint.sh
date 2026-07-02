#!/bin/sh
set -eu

# ---------------------------------------------------------------------------
# robotsix-auto-mail entrypoint — pre-flight validation before handing off
# to the Python CLI.
#
# Configuration is loaded from a single YAML config file only. The file is
# located via MAIL_CONFIG_PATH (default: config/mail.local.yaml); the deploy
# sets MAIL_CONFIG_PATH=/home/mailbot/config/config.yaml.
# ---------------------------------------------------------------------------

# Bypass config checks for flags/commands that should never require config.
case "${1-}" in
    -h|--help|-V|--version|""|detect) exec robotsix-auto-mail "$@" ;;
esac

# ---------------------------------------------------------------------------
# Pre-flight validation — a readable YAML config file is required.
# ---------------------------------------------------------------------------

_CONFIG_PATH="${MAIL_CONFIG_PATH:-config/mail.local.yaml}"

if [ ! -r "${_CONFIG_PATH}" ]; then
    cat >&2 <<EOF
Missing configuration file: ${_CONFIG_PATH}

robotsix-auto-mail loads its configuration from a single YAML file located
via MAIL_CONFIG_PATH (default: config/mail.local.yaml).

Provide a readable config file at that path, e.g.:
  cp docs/config/mail.local.example.yaml config/mail.local.yaml

or auto-generate one from your email address:
  robotsix-auto-mail detect user@example.com
EOF
    exit 1
fi

export MAIL_CONFIG_PATH="${_CONFIG_PATH}"

# ---------------------------------------------------------------------------
# Launch the application (replaces this shell process)
# ---------------------------------------------------------------------------

exec robotsix-auto-mail "$@"
