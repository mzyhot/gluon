#!/bin/bash
# Apply Gluon's changes to the published Wine source. Never silently skip drift.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="${1:?Wine source directory required}"
PATCH="$ROOT/packaging/patches/wine-opengl-promoted-aliases.patch"
if patch --dry-run --forward --batch -p1 -d "$SOURCE" < "$PATCH" >/dev/null 2>&1; then
    patch --forward --batch -p1 -d "$SOURCE" < "$PATCH"
elif patch --dry-run --reverse --batch -p1 -d "$SOURCE" < "$PATCH" >/dev/null 2>&1; then
    echo "Wine OpenGL alias patch already applied"
else
    echo "Wine source differs from the supported OpenGL patch; refusing to continue" >&2
    exit 1
fi
