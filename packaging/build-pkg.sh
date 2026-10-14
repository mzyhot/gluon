#!/bin/sh
# Build dist/Gluon-<version>.pkg: a per-user installer (no administrator password).
#
# It installs into ~/Library/Application Support/Gluon:
#   bin/gluon            launcher Steam starts for Windows games
#   bin/gluon-agent      background helper (run by launchd every minute)
#   bin/gluon-uninstall
#   lib/gluon/           the Python package
#   runtime/<name>/      the Windows runtime, with runtime/current pointing at it
# and its postinstall script registers ~/Library/LaunchAgents/com.gluon.agent.plist.
#
# Usage: packaging/build-pkg.sh [version]
# Env:   GLUON_PKG_RUNTIME  runtime folder to bundle
#                           (default: what ~/Library/Application Support/Gluon/runtime/current points at)
set -e

version="${1:-0.1.0}"
here="$(cd "$(dirname "$0")/.." && pwd)"
runtime_src="${GLUON_PKG_RUNTIME:-$(cd "$HOME/Library/Application Support/Gluon/runtime/current" && pwd -P)}"
[ -x "$runtime_src/proton" ] || [ -x "$runtime_src/bin/wine" ] || { echo "no runtime at $runtime_src" >&2; exit 1; }
[ -d "$runtime_src/licenses" ] || { echo "runtime at $runtime_src has no licenses folder; refusing to package it" >&2; exit 1; }
runtime_name="$(basename "$runtime_src")"

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
root="$stage/root/Library/Application Support/Gluon"
mkdir -p "$root/bin" "$root/lib/gluon" "$root/runtime" "$here/dist"

install -m 755 "$here/tool/gluon" "$here/packaging/gluon-agent" "$here/packaging/gluon-uninstall" "$root/bin/"
install -m 644 "$here"/gluon/*.py "$root/lib/gluon/"
# Left out: prefix templates and locks a runtime creates on first use, and a bundled
# runtime's own separate launcher program (Gluon is driven by Steam, not by an app).
rsync -a --exclude '/files/share/default_pfx' --exclude '/*.lock' --exclude '/bin/pmac' \
    "$runtime_src/" "$root/runtime/$runtime_name/"
ln -s "$runtime_name" "$root/runtime/current"

pkgbuild --quiet --root "$stage/root" --identifier com.gluon.pkg --version "$version" \
    --scripts "$here/packaging/scripts" --install-location / "$stage/gluon-component.pkg"

cat > "$stage/distribution.xml" <<XML
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
    <title>Gluon</title>
    <options customize="never" require-scripts="false" hostArchitectures="arm64"/>
    <domains enable_anywhere="false" enable_currentUserHome="true" enable_localSystem="false"/>
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
