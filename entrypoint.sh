#!/bin/sh
set -eu

# Bypass config setup for commands that need no config file.
case "${1-}" in
    -h|--help|-V|--version|"") exec robotsix-auto-mail "$@" ;;
esac

exec robotsix-auto-mail "$@"
