#!/bin/bash
# Rebuild only Proton's steam.exe with Gluon's ownership fixes. The bridge DLLs
# retain their original ABI and bytes; this module does not import lsteamclient.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$ROOT/packaging/require-cloud.sh"
WORK="${WORK:-$ROOT/build}"
OUT="${OUT:-$WORK/gluon-wine}"
WINE_OBJ="${WINE_OBJ:-$WORK/wine-build}"
WINE_SRC="${WINE_SRC:-$WORK/src/wine}"
JOBS="${JOBS:-4}"
STEAM_HELPER_REV="823f91a4215104d3c149d243c827c1152f33ee9b"
STEAM_HELPER_C_SHA="1092e80ab6a3e0a5e3dc1bd15e94afd5e5d3ddc5a4124c205c94441cd4d11ae2"
STEAM_HELPER_MAKE_SHA="68db082a9be8c943ee5873d6bb4d94af1318c1f108a435b09aa84560276cc2e9"
GLUON_HELPER_SOURCE="$WORK/steam-helper-source"
GLUON_HELPER_MODULE="programs/gluon-steam-helper"

fetch() {
    local file="$1" expected="$2" path="$WORK/downloads/steam-helper-$1"
    if [ ! -f "$path" ] || ! printf '%s  %s\n' "$expected" "$path" | shasum -a 256 -c - >/dev/null 2>&1; then
        curl -fL --retry 3 --retry-delay 5 --connect-timeout 15 --max-time 180 \
            "https://raw.githubusercontent.com/albvalort/Proton/$STEAM_HELPER_REV/steam_helper/$file" -o "$path"
    fi
    printf '%s  %s\n' "$expected" "$path" | shasum -a 256 -c -
    cp "$path" "$GLUON_HELPER_SOURCE/$file"
}

mkdir -p "$WORK/downloads" "$GLUON_HELPER_SOURCE"
fetch steam.c "$STEAM_HELPER_C_SHA"
fetch Makefile.in "$STEAM_HELPER_MAKE_SHA"
patch --batch --forward -d "$GLUON_HELPER_SOURCE" -p1 < "$ROOT/packaging/patches/steam-helper-resource-ownership.patch"

if [ "${1:-}" = --source-only ]; then
    printf '%s\n' "$GLUON_HELPER_SOURCE"
    exit 0
fi
[ "$#" -eq 0 ] || { echo 'Usage: build-steam-helper.sh [--source-only]' >&2; exit 2; }
[ -f "$WINE_OBJ/Makefile" ] && [ -x "$WINE_OBJ/tools/makedep" ] || {
    echo 'The configured Wine build and its makedep tool are required.' >&2; exit 1;
}

# Reuse Wine's configured PE compilers, include directories and import libraries.
# Add one program to its generated dependency graph, then request only its two
# PE targets. Unlike the Proton fork's external-module extension, this works
# with the published Wine 11 makedep without changing that tool or bridge DLLs.
[ -d "$WINE_SRC/include" ] || { echo 'Wine source headers are required.' >&2; exit 1; }
mkdir -p "$WINE_SRC/$GLUON_HELPER_MODULE"
cp "$GLUON_HELPER_SOURCE/steam.c" "$GLUON_HELPER_SOURCE/Makefile.in" "$WINE_SRC/$GLUON_HELPER_MODULE/"
python3 - "$WINE_OBJ/Makefile" "$GLUON_HELPER_MODULE" <<'PY'
from pathlib import Path
import re
import sys
path, module = Path(sys.argv[1]), sys.argv[2]
source = path.read_text()
match = re.search(r'^SUBDIRS\s*=\s*((?:[^\n]*\\\n)*[^\n]*)', source, re.MULTILINE)
if not match:
    raise SystemExit('Wine Makefile has no expected SUBDIRS assignment')
if module not in match.group(1).replace('\\\n', ' ').split():
    source = source[:match.start(1)] + module + ' ' + source[match.start(1):]
    path.write_text(source)
PY
(cd "$WINE_OBJ" && ./tools/makedep)
make -C "$WINE_OBJ" -j"$JOBS" \
    "$GLUON_HELPER_MODULE/i386-windows/steam.exe" \
    "$GLUON_HELPER_MODULE/x86_64-windows/steam.exe"

for arch in i386 x86_64; do
    binary="$WINE_OBJ/$GLUON_HELPER_MODULE/$arch-windows/steam.exe"
    [ -f "$binary" ] || { echo "Missing rebuilt $arch steam.exe" >&2; exit 1; }
    install -m 644 "$binary" "$OUT/lib/wine/$arch-windows/steam.exe"
done

mkdir -p "$OUT/licenses/steam-helper"
cp "$GLUON_HELPER_SOURCE/steam.c" "$GLUON_HELPER_SOURCE/Makefile.in" "$OUT/licenses/steam-helper/"
cp "$ROOT/packaging/patches/steam-helper-resource-ownership.patch" "$OUT/licenses/steam-helper/"
cat > "$OUT/licenses/steam-helper/SOURCES.txt" <<EOF
Proton steam.exe launcher helper, rebuilt by Gluon from public source:
https://github.com/albvalort/Proton/tree/$STEAM_HELPER_REV/steam_helper
steam.c SHA-256 before Gluon patch: $STEAM_HELPER_C_SHA
Makefile.in SHA-256: $STEAM_HELPER_MAKE_SHA
Copyright and BSD redistribution terms are preserved in steam.c.
Gluon modifications: steam-helper-resource-ownership.patch
Built against the same Wine configuration as this runtime for i386 and x86_64.
Only steam.exe is replaced; the existing lsteamclient modules are preserved.
EOF
