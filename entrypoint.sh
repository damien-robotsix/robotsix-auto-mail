#!/bin/sh
set -eu

# envsubst: substitute ${VAR} placeholders from env into the config file.

# Bypass config setup for commands that need no config file.
case "${1-}" in
    -h|--help|-V|--version|"") exec robotsix-auto-mail "$@" ;;
esac

_TEMP_CONFIG=""

cleanup() {
    if [ -n "${_TEMP_CONFIG}" ] && [ -f "${_TEMP_CONFIG}" ]; then
        rm -f "${_TEMP_CONFIG}"
    fi
}
trap cleanup EXIT

# Optional envsubst: substitute ${VAR} placeholders in the config file.
_CONFIG_PATH="${ROBOTSIX_CONFIG_FILE:-config/config.json}"
if command -v envsubst >/dev/null 2>&1; then
    _TEMP_CONFIG="$(mktemp /tmp/mail-config.XXXXXX)"
    envsubst < "${_CONFIG_PATH}" > "${_TEMP_CONFIG}"
    ROBOTSIX_CONFIG_FILE="${_TEMP_CONFIG}"
    export ROBOTSIX_CONFIG_FILE
fi

exec robotsix-auto-mail "$@"
