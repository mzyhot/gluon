#!/bin/bash
# Package upstream shared Wine Mono/Gecko releases without executing installers.
# Usage: build-addons.sh <published-Wine-source-directory> <runtime>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$ROOT/packaging/require-cloud.sh"
WINE_SOURCE="${1:?Wine source directory required}"
OUT="${2:?runtime directory required}"
WORK="${WORK:-$ROOT/build}"
DL="$WORK/downloads"
mkdir -p "$DL" "$OUT/share/wine/mono" "$OUT/share/wine/gecko" "$OUT/licenses/wine-addons"
python3 - "$WINE_SOURCE" <<'PY'
from pathlib import Path
import re, sys
source = Path(sys.argv[1])
addons = (source / 'dlls/appwiz.cpl/addons.c').read_text()
mscoree = (source / 'dlls/mscoree/mscoree_private.h').read_text()
expected = {'MONO_VERSION': '10.4.1', 'GECKO_VERSION': '2.47.4'}
for macro, version in expected.items():
    if not re.search(r'#define\s+' + macro + r'\s+"' + re.escape(version) + r'"', addons):
        raise SystemExit('Wine add-on source versions changed: ' + macro)
if '#define WINE_MONO_VERSION "10.4.1"' not in mscoree:
    raise SystemExit('Wine CLR host and Mono release versions differ')
PY
fetch() {
    if [ ! -f "$DL/$3" ] || ! echo "$2  $DL/$3" | shasum -a 256 -c - >/dev/null 2>&1; then
        curl -fL --retry 3 --retry-delay 5 -o "$DL/$3" "$1"
    fi
    echo "$2  $DL/$3" | shasum -a 256 -c -
}
MONO_BASE=https://github.com/wine-mono/wine-mono/releases/download/wine-mono-10.4.1
GECKO_BASE=https://dl.winehq.org/wine/wine-gecko/2.47.4
fetch "$MONO_BASE/wine-mono-10.4.1-x86.tar.xz" \
    a16606ef0724202e6a6848ece6e0cbba64d11e2f11aefe744af1d93c6d9f99bb wine-mono-10.4.1-x86.tar.xz
fetch "$MONO_BASE/wine-mono-10.4.1-src.tar.xz" \
    128e335396689e466dff5e1aa0770a714dbe3d16397fc4ddbb8a80999c67323b wine-mono-10.4.1-src.tar.xz
fetch "$GECKO_BASE/wine-gecko-2.47.4-x86.tar.xz" \
    2cfc8d5c948602e21eff8a78613e1826f2d033df9672cace87fed56e8310afb6 wine-gecko-2.47.4-x86.tar.xz
fetch "$GECKO_BASE/wine-gecko-2.47.4-x86_64.tar.xz" \
    fd88fc7e537d058d7a8abf0c1ebc90c574892a466de86706a26d254710a82814 wine-gecko-2.47.4-x86_64.tar.xz
tar -xJf "$DL/wine-mono-10.4.1-x86.tar.xz" -C "$OUT/share/wine/mono"
tar -xJf "$DL/wine-gecko-2.47.4-x86.tar.xz" -C "$OUT/share/wine/gecko"
tar -xJf "$DL/wine-gecko-2.47.4-x86_64.tar.xz" -C "$OUT/share/wine/gecko"
python3 "$ROOT/packaging/extract-addon-notices.py" "$DL/wine-mono-10.4.1-src.tar.xz" \
    "$OUT/share/wine/gecko/wine-gecko-2.47.4-x86/omni.ja" "$OUT/licenses/wine-addons"
for name in mono/wine-mono-10.4.1/support/winemono-support.msi \
    mono/wine-mono-10.4.1/bin/libmono-2.0-x86.dll \
    mono/wine-mono-10.4.1/bin/libmono-2.0-x86_64.dll \
    gecko/wine-gecko-2.47.4-x86/xul.dll gecko/wine-gecko-2.47.4-x86_64/xul.dll; do
    [ -f "$OUT/share/wine/$name" ] || { echo "Incomplete Wine add-on: $name" >&2; exit 1; }
done
cat > "$OUT/licenses/wine-addons/SOURCES.txt" <<'EOF'
Shared runtime files come only from Wine's own public open-source releases:
Wine Mono 10.4.1 (both x86 and x86_64 runtimes in the shared x86 archive)
https://github.com/wine-mono/wine-mono/releases/tag/wine-mono-10.4.1
Runtime archive SHA-256 a16606ef0724202e6a6848ece6e0cbba64d11e2f11aefe744af1d93c6d9f99bb
Complete matching source archive SHA-256 128e335396689e466dff5e1aa0770a714dbe3d16397fc4ddbb8a80999c67323b
The matching source's component license and copyright notices are preserved in mono-source/.

Wine Gecko 2.47.4
https://dl.winehq.org/wine/wine-gecko/2.47.4/wine-gecko-2.47.4-x86.tar.xz
SHA-256 2cfc8d5c948602e21eff8a78613e1826f2d033df9672cace87fed56e8310afb6
https://dl.winehq.org/wine/wine-gecko/2.47.4/wine-gecko-2.47.4-x86_64.tar.xz
SHA-256 fd88fc7e537d058d7a8abf0c1ebc90c574892a466de86706a26d254710a82814
Complete source: https://dl.winehq.org/wine/wine-gecko/2.47.4/wine-gecko-2.47.4-src.tar.bz2
Gecko's embedded about:license page is preserved intact and copied into gecko/.
Both official Gecko archives retain all their upstream notices and bundled components.

Versions match the public Wine source's appwiz.cpl/addons.c and mscoree host.
No commercial runtime files, prefix files or user-installed add-ons are copied.
No MSI or Wine executable runs during packaging.
EOF
printf '%s\n' 'wine-mono-10.4.1 wine-gecko-2.47.4-x86 wine-gecko-2.47.4-x86_64' > "$OUT/.gluon-addons"
echo 'Official shared Wine Mono and Gecko add-ons packaged.'
