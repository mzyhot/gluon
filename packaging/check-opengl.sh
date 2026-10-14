#!/bin/bash
# Compile and run the native CGL probe.
# Usage: check-opengl.sh [--compile-only] [arm64|x86_64 ...]
set -euo pipefail

GLUON_PROBE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$GLUON_PROBE_ROOT/packaging/require-cloud.sh"
GLUON_PROBE_TEMP="$(mktemp -d "${TMPDIR:-/tmp}/gluon-opengl.XXXXXX")"
trap 'rm -rf "$GLUON_PROBE_TEMP"' EXIT

GLUON_PROBE_COMPILE_ONLY=0
if [ "${1:-}" = --compile-only ]; then
    GLUON_PROBE_COMPILE_ONLY=1
    shift
fi
if [ "$#" -eq 0 ]; then set -- "$(uname -m)"; fi
for GLUON_PROBE_ARCH in "$@"; do
    case "$GLUON_PROBE_ARCH" in
        arm64|x86_64) ;;
        *) printf 'Unsupported architecture: %s\n' "$GLUON_PROBE_ARCH" >&2; exit 2 ;;
    esac
    xcrun clang -std=c11 -Wall -Wextra -Werror -arch "$GLUON_PROBE_ARCH" \
        "$GLUON_PROBE_ROOT/packaging/check-opengl.c" -framework OpenGL \
        -o "$GLUON_PROBE_TEMP/check-opengl-$GLUON_PROBE_ARCH"
    if [ "$GLUON_PROBE_COMPILE_ONLY" -eq 1 ]; then
        printf 'Compiled native OpenGL probe for %s\n' "$GLUON_PROBE_ARCH"
    else
        "$GLUON_PROBE_TEMP/check-opengl-$GLUON_PROBE_ARCH"
    fi
done
