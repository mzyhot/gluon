#!/bin/sh
# Register a shim in the place the Steam client scans for compatibility tools.
# The shim always executes Gluon's installed launcher, including after updates.
# This explicitly writes inside Steam's bundle; the package does not do so.
# Steam reads the folder at startup, so restart Steam afterwards.
set -e

here="$(cd "$(dirname "$0")/.." && pwd)"
steam="${STEAM_ROOT:-$HOME/Library/Application Support/Steam}"
dest="$steam/Steam.AppBundle/Steam/Contents/MacOS/compatibilitytools.d/gluon"

[ -d "$steam/Steam.AppBundle" ] || { echo "Steam not found at $steam" >&2; exit 1; }

mkdir -p "$dest"
cp -p "$here/tool/compatibilitytool.vdf" "$here/tool/toolmanifest.vdf" "$dest/"
install -m 755 "$here/tool/gluon-shim" "$dest/gluon"
echo "installed Gluon tool into $dest"
ls -la "$dest"
