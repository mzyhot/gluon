#!/bin/bash
# Apply Gluon's changes to the published Wine source. Never silently skip drift.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PATCH_NAMES=(wine-opengl-promoted-aliases wine-vulkan-portability wine-macos-display-name wine-gl-compat-as-core wine-macos-app-bundle wine-macos-overlay-scope wine-font-rejection-ownership wine-resolver-fallback-ownership)
if [ "${1:-}" = --list ]; then
    printf '%s\n' "${PATCH_NAMES[@]}"
    exit 0
fi
if [ "${1:-}" = --hash ]; then
    for name in "${PATCH_NAMES[@]}"; do
        printf '%s %s\n' "$name" "$(shasum -a 256 "$ROOT/packaging/patches/$name.patch" | cut -d' ' -f1)"
    done | shasum -a 256 | cut -d' ' -f1
    exit 0
fi
SOURCE="${1:?Wine source directory required}"
for name in "${PATCH_NAMES[@]}"; do
    PATCH="$ROOT/packaging/patches/$name.patch"
    if patch --dry-run --forward --batch --fuzz=0 -p1 -d "$SOURCE" < "$PATCH" >/dev/null 2>&1; then
        patch --forward --batch --fuzz=0 -p1 -d "$SOURCE" < "$PATCH"
    elif patch --dry-run --reverse --batch --fuzz=0 -p1 -d "$SOURCE" < "$PATCH" >/dev/null 2>&1; then
        echo "$name already applied"
    else
        echo "Wine source differs from the supported $name patch; refusing to continue" >&2
        exit 1
    fi
done
