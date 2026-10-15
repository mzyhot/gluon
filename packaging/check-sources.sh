#!/bin/bash
# Cloud-only source checks. One isolated string helper is compiled and tested;
# no Wine process, native graphics library, game, Steam installation, or
# commercial-product binary is used.
# Run in a GitHub Codespace or GitHub Actions Linux runner:
#   timeout 19m bash packaging/check-sources.sh
set -euo pipefail

GLUON_SOURCE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$GLUON_SOURCE_ROOT/packaging/require-cloud.sh"
if [ "$(uname -s)" != Linux ]; then
    echo "Source checks require a Linux GitHub cloud host." >&2
    exit 1
fi

GLUON_SOURCE_WORK="${GLUON_SOURCE_WORK:-$GLUON_SOURCE_ROOT/build/source-checks}"
GLUON_SOURCE_DOWNLOADS="$GLUON_SOURCE_ROOT/build/downloads"
mkdir -p "$GLUON_SOURCE_WORK" "$GLUON_SOURCE_DOWNLOADS"
GLUON_SOURCE_TEMP="$(mktemp -d "$GLUON_SOURCE_WORK/tree.XXXXXX")"
trap 'rm -rf "$GLUON_SOURCE_TEMP"' EXIT

echo "Checking tracked shell scripts without running them"
while IFS= read -r -d '' script; do
    bash -n "$GLUON_SOURCE_ROOT/$script"
done < <(git -C "$GLUON_SOURCE_ROOT" ls-files -z -- '*.sh')

echo "Parsing tracked Python sources without importing them"
python3 - "$GLUON_SOURCE_ROOT" <<'PY'
import ast
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1])
files = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z', '--', '*.py'])
for item in files.split(b'\0'):
    if item:
        path = root / item.decode()
        ast.parse(path.read_text(), filename=str(path))
print('Tracked Python syntax passed')
PY

# Read the same published-source pin as the runtime build without executing it.
GLUON_SOURCE_URL="$(sed -n 's/^CX_SRC_URL="\([^"]*\)"$/\1/p' "$GLUON_SOURCE_ROOT/packaging/build-runtime.sh")"
GLUON_SOURCE_SHA="$(sed -n 's/^CX_SRC_SHA="\([^"]*\)"$/\1/p' "$GLUON_SOURCE_ROOT/packaging/build-runtime.sh")"
if [[ "$GLUON_SOURCE_URL" == *$'\n'* ]] || [[ ! "$GLUON_SOURCE_SHA" =~ ^[[:xdigit:]]{64}$ ]]; then
    echo "Expected one literal published-source URL and SHA-256 in build-runtime.sh" >&2
    exit 1
fi
case "$GLUON_SOURCE_URL" in
    https://media.codeweavers.com/pub/crossover/source/crossover-sources-*.tar.gz) ;;
    *) echo "Unexpected published-source URL: $GLUON_SOURCE_URL" >&2; exit 1 ;;
esac
GLUON_SOURCE_ARCHIVE="$GLUON_SOURCE_DOWNLOADS/crossover-sources.tar.gz"
if [ ! -f "$GLUON_SOURCE_ARCHIVE" ] || ! printf '%s  %s\n' "$GLUON_SOURCE_SHA" "$GLUON_SOURCE_ARCHIVE" | shasum -a 256 -c - >/dev/null 2>&1; then
    echo "Downloading pinned published open-source archive"
    curl -fL --connect-timeout 15 --max-time 300 --retry 2 --retry-delay 5 \
        --retry-max-time 600 -o "$GLUON_SOURCE_TEMP/source.tar.gz" "$GLUON_SOURCE_URL"
    printf '%s  %s\n' "$GLUON_SOURCE_SHA" "$GLUON_SOURCE_TEMP/source.tar.gz" | shasum -a 256 -c -
    mv "$GLUON_SOURCE_TEMP/source.tar.gz" "$GLUON_SOURCE_ARCHIVE"
fi
printf '%s  %s\n' "$GLUON_SOURCE_SHA" "$GLUON_SOURCE_ARCHIVE" | shasum -a 256 -c -

# Extract only text needed by the checks, rather than a full runtime source tree.
tar -xzf "$GLUON_SOURCE_ARCHIVE" -C "$GLUON_SOURCE_TEMP" --strip-components 1 \
    sources/wine/dlls/opengl32/unix_wgl.c \
    sources/wine/dlls/opengl32/thunks.c \
    sources/wine/dlls/opengl32/unix_thunks.c \
    sources/wine/dlls/win32u/vulkan.c \
    sources/wine/dlls/ntdll/unix/loader.c \
    sources/wine/include/wine/vulkan.h \
    sources/dxvk/src/util/config/config.cpp \
    sources/dxvk/src/d3d9/d3d9_include.h \
    sources/dxvk/src/d3d9/d3d9_device.cpp

echo "Applying Wine source patch and checking repeat application"
bash "$GLUON_SOURCE_ROOT/packaging/apply-wine-patches.sh" "$GLUON_SOURCE_TEMP/wine"
bash "$GLUON_SOURCE_ROOT/packaging/apply-wine-patches.sh" "$GLUON_SOURCE_TEMP/wine"
bash "$GLUON_SOURCE_ROOT/packaging/check-opengl-aliases.sh" "$GLUON_SOURCE_TEMP/wine" "$GLUON_SOURCE_DOWNLOADS"
bash "$GLUON_SOURCE_ROOT/packaging/check-vulkan-portability.sh" "$GLUON_SOURCE_TEMP/wine" "$GLUON_SOURCE_DOWNLOADS"

echo "Checking the exact Wine display-name string helper"
if [ "${GITHUB_ACTIONS:-}" = true ]; then
    python3 "$GLUON_SOURCE_ROOT/packaging/check-wine-display-name.py" "$GLUON_SOURCE_TEMP/wine" --self-test
else
    python3 "$GLUON_SOURCE_ROOT/packaging/check-wine-display-name.py" "$GLUON_SOURCE_TEMP/wine" --source-only --self-test
fi

echo "Checking DXVK patch applicability against the pinned source"
for patch_name in dxvk-explicit-algorithm dxvk-mingw-resource-manager dxvk-supported-metal-features; do
    patch_file="$GLUON_SOURCE_ROOT/packaging/patches/$patch_name.patch"
    patch --dry-run --forward --batch --fuzz=0 -p1 -d "$GLUON_SOURCE_TEMP/dxvk" < "$patch_file"
    patch --forward --batch --fuzz=0 -p1 -d "$GLUON_SOURCE_TEMP/dxvk" < "$patch_file"
    patch --dry-run --reverse --batch --fuzz=0 -p1 -d "$GLUON_SOURCE_TEMP/dxvk" < "$patch_file"
done

echo "Cloud source checks passed; runtime compilation and rendering are untested by this job."
