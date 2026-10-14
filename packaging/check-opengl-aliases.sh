#!/bin/bash
# Fetch one pinned Khronos registry input and check an already patched Wine tree.
# Usage: check-opengl-aliases.sh <wine-source> [download-cache]
set -euo pipefail

GLUON_ALIAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GLUON_ALIAS_SOURCE="${1:?Wine source directory required}"
GLUON_ALIAS_CACHE="${2:-$GLUON_ALIAS_ROOT/build/downloads}"
GLUON_ALIAS_COMMIT=1cdd228e34966dd6b95bd203e9f84faba0f371a1
GLUON_ALIAS_SHA=b9ca2cfa5c676e901c20d34af3407f1687cde0f1336a5ff7a8974d04c7494ad3
GLUON_ALIAS_URL="https://raw.githubusercontent.com/KhronosGroup/OpenGL-Registry/$GLUON_ALIAS_COMMIT/xml/gl.xml"
GLUON_ALIAS_REGISTRY="$GLUON_ALIAS_CACHE/gl-registry-$GLUON_ALIAS_COMMIT.xml"

mkdir -p "$GLUON_ALIAS_CACHE"
if [ ! -f "$GLUON_ALIAS_REGISTRY" ]; then
    GLUON_ALIAS_TEMP="$(mktemp "$GLUON_ALIAS_CACHE/.gl-registry.XXXXXX")"
    trap 'rm -f "$GLUON_ALIAS_TEMP"' EXIT
    curl -fL --connect-timeout 30 --max-time 120 -o "$GLUON_ALIAS_TEMP" "$GLUON_ALIAS_URL"
    printf '%s  %s\n' "$GLUON_ALIAS_SHA" "$GLUON_ALIAS_TEMP" | shasum -a 256 -c -
    mv "$GLUON_ALIAS_TEMP" "$GLUON_ALIAS_REGISTRY"
fi
printf '%s  %s\n' "$GLUON_ALIAS_SHA" "$GLUON_ALIAS_REGISTRY" | shasum -a 256 -c -
python3 "$GLUON_ALIAS_ROOT/packaging/check-opengl-aliases.py" \
    "$GLUON_ALIAS_SOURCE" "$GLUON_ALIAS_REGISTRY" --applied --self-test
