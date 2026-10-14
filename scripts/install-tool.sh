#!/bin/sh
# Copy the Gluon tool folder into the place the Steam client scans for
# compatibility tools. Re-run after editing anything under tool/.
# Steam reads the folder at startup, so restart Steam afterwards.
set -e

here="$(cd "$(dirname "$0")/.." && pwd)"
steam="${STEAM_ROOT:-$HOME/Library/Application Support/Steam}"
dest="$steam/Steam.AppBundle/Steam/Contents/MacOS/compatibilitytools.d/gluon"

[ -d "$steam/Steam.AppBundle" ] || { echo "Steam not found at $steam" >&2; exit 1; }

mkdir -p "$dest"
cp -p "$here/tool/compatibilitytool.vdf" "$here/tool/toolmanifest.vdf" "$here/tool/gluon" "$dest/"
chmod +x "$dest/gluon"
echo "installed Gluon tool into $dest"
ls -la "$dest"
