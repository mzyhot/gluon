#!/bin/bash
# Only synthetic Mach-O images; does not load Steam or a game.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ "${GITHUB_ACTIONS:-}" != true ]; then
    echo 'Native Steam compatibility tests compile only on GitHub Actions.' >&2
    exit 1
fi
WORK="$(mktemp -d "${TMPDIR:-/tmp}/gluon-steam-compat.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
"${CC:-cc}" -std=c11 -Wall -Wextra -Werror -pedantic -O1 -g \
    -fsanitize=address,undefined -fno-omit-frame-pointer \
    "$ROOT/native/steam-compat/resolver.c" "$ROOT/native/steam-compat/test-resolver.c" \
    -o "$WORK/test-resolver"
"$WORK/test-resolver"
