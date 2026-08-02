#!/usr/bin/env bash
# Vendor the shared config panel from @robotsix/ui into the served static dir.
#
# The panel is the fleet's one settings renderer (robotsix-standards
# config-ownership.md).  Its build output is not committed — the image build
# does this same copy in its `ui` stage; run this for a local checkout.
set -euo pipefail

VERSION="${ROBOTSIX_UI_VERSION:-v0.1.7}"
DEST="$(dirname "$0")/../src/robotsix_auto_mail/server/static"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Installing @robotsix/ui#${VERSION}…"
(cd "$WORK" && npm install --no-save --silent "github:damien-robotsix/robotsix-ui#${VERSION}")

cp "$WORK/node_modules/@robotsix/ui/dist/vanilla.js" "$DEST/robotsix-ui.js"
cp "$WORK/node_modules/@robotsix/ui/dist/style.css" "$DEST/robotsix-ui.css"

echo "Vendored robotsix-ui ${VERSION} into ${DEST}"
