#!/bin/sh
# Build dist/Gluon-<version>.pkg: system staging plus unprivileged per-user setup.
#
# It installs into ~/Library/Application Support/Gluon:
#   bin/gluon            launcher Steam starts for Windows games
#   bin/gluon-agent      background helper (run by launchd every minute)
#   bin/gluon-uninstall
#   lib/gluon/           the Python package
#   runtime/<unique-name>/  complete runtime; the idle agent selects it atomically
# and its postinstall script registers ~/Library/LaunchAgents/com.gluon.agent.plist.
#
# Usage: packaging/build-pkg.sh [version]
# Env:   GLUON_PKG_RUNTIME  runtime folder to bundle
#                           (default: what ~/Library/Application Support/Gluon/runtime/current points at)
set -e

version="${1:-0.1.0}"
case "$version" in
    '' | *[!A-Za-z0-9_.-]*) echo "Invalid package version" >&2; exit 1 ;;
esac
here="$(cd "$(dirname "$0")/.." && pwd)"
. "$here/packaging/require-cloud.sh"
launcher_host="${GLUON_PKG_LAUNCHER_HOST:-$here/build/launcher-host/gluon-launcher-host}"
[ -x "$launcher_host" ] || { echo "tested native launcher host missing: $launcher_host" >&2; exit 1; }
runtime_src="${GLUON_PKG_RUNTIME:-$(cd "$HOME/Library/Application Support/Gluon/runtime/current" && pwd -P)}"
[ -x "$runtime_src/proton" ] || [ -x "$runtime_src/bin/wine" ] || { echo "no runtime at $runtime_src" >&2; exit 1; }
[ -d "$runtime_src/licenses" ] || { echo "runtime at $runtime_src has no licenses folder; refusing to package it" >&2; exit 1; }
# A retry has its own directory as well, so an installation cannot overwrite
# native modules that an existing Wine session is using.
run_id="${GITHUB_RUN_ID:?Package creation requires an Actions run ID}"
run_attempt="${GITHUB_RUN_ATTEMPT:-1}"
case "$run_id.$run_attempt" in *[!0-9.]*) echo "Invalid Actions run identity" >&2; exit 1 ;; esac
runtime_name="gluon-runtime-$version-$run_id.$run_attempt"

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
root="$stage/root/Library/Application Support/Gluon"
steamplay_src="$here/build/steamplay-payload"
[ -f "$steamplay_src/payload.json" ] || { echo 'Steam Play payload is missing' >&2; exit 1; }
steamplay_name="$version-$run_id.$run_attempt"
mkdir -p "$root/bin" "$root/lib/gluon" "$root/runtime" "$root/steamplay/payloads/$steamplay_name" "$here/dist"

install -m 755 "$here/tool/gluon" "$here/packaging/gluon-agent" "$here/packaging/gluon-uninstall" "$here/packaging/gluonctl" "$root/bin/"
install -m 755 "$launcher_host" "$root/bin/gluon-launcher-host"
install -m 644 "$here"/gluon/*.py "$root/lib/gluon/"
# Explicitly remove retired modules on upgrade (pkg receipts do not remove them).
rsync -a "$steamplay_src/" "$root/steamplay/payloads/$steamplay_name/"
printf '{"name":"%s"}\n' "$steamplay_name" > "$root/steamplay/pending.json"
# Left out: prefix templates and locks a runtime creates on first use, and a bundled
# runtime's own separate launcher program (Gluon is driven by Steam, not by an app).
rsync -a --exclude '/files/share/default_pfx' --exclude '/*.lock' --exclude '/bin/pmac' \
    "$runtime_src/" "$root/runtime/$runtime_name/"
if [ -f "$runtime_src/.gluon-wine-patches" ]; then
    expected_patches="$("$here/packaging/apply-wine-patches.sh" --hash)"
    [ "$(cat "$runtime_src/.gluon-wine-patches")" = "$expected_patches" ] \
        || { echo "runtime Wine patches do not match this checkout" >&2; exit 1; }
    wine_patch_names="$("$here/packaging/apply-wine-patches.sh" --list)"
    patch_notices="$root/runtime/$runtime_name/licenses/gluon-wine-patches"
    mkdir -p "$patch_notices"
    for patch_name in $wine_patch_names; do
        install -m 644 "$here/packaging/patches/$patch_name.patch" "$patch_notices/"
    done
    printf '%s\n' "$wine_patch_names" > "$patch_notices/APPLIED.txt"
fi
printf '{"schema":1,"name":"%s","version":"%s"}\n' "$runtime_name" "$version" \
    > "$root/runtime/pending.json"

pkgbuild --quiet --root "$stage/root" --identifier com.gluon.pkg --version "$version" \
    --scripts "$here/packaging/scripts" --install-location / "$stage/gluon-component.pkg"

cat > "$stage/distribution.xml" <<XML
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
    <title>Gluon</title>
    <options customize="never" require-scripts="true" hostArchitectures="arm64"/>
    <domains enable_anywhere="false" enable_currentUserHome="false" enable_localSystem="true"/>
    <choices-outline>
        <line choice="gluon"/>
    </choices-outline>
    <choice id="gluon" visible="false">
        <pkg-ref id="com.gluon.pkg"/>
    </choice>
    <pkg-ref id="com.gluon.pkg" version="$version" onConclusion="none">gluon-component.pkg</pkg-ref>
</installer-gui-script>
XML

out="$here/dist/Gluon-$version.pkg"
productbuild --quiet --distribution "$stage/distribution.xml" --package-path "$stage" "$out"
echo "built $out"
ls -la "$out"
