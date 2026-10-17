#!/bin/bash
# Build the read-only developer probe on GitHub Actions only.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ "${GITHUB_ACTIONS:-}" != true ] || [ "$(uname -s)" != Darwin ]; then
    echo 'The native Steam compatibility probe builds only on macOS GitHub Actions.' >&2
    exit 1
fi
OUT="${1:-$ROOT/build/steam-compat}"
mkdir -p "$OUT"
xcrun clang -std=c11 -Wall -Wextra -Werror -O2 -fvisibility=hidden \
    -arch arm64 -arch x86_64 -mmacosx-version-min=11.0 -dynamiclib \
    "$ROOT/native/steam-compat/resolver.c" "$ROOT/native/steam-compat/probe.c" \
    -install_name @rpath/libgluon-steam-compat.dylib \
    -o "$OUT/libgluon-steam-compat.dylib"
echo 'Built disabled Steam compatibility developer probe (no hooks).'
