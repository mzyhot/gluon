#!/bin/bash
# Validate the bundled native Vulkan library and build both Windows ABI probes.
# No game, user prefix, or Steam process is touched. --device is an optional manual
# probe: CI machines and sandboxed tools may have no accessible Metal device.
# Usage: check-vulkan.sh RUNTIME WINE_SOURCE LLVM_MINGW OUTPUT_DIRECTORY
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$ROOT/packaging/require-cloud.sh"
RUNTIME="$1"
WINE_SOURCE="$2"
LLVM_MINGW="$3"
PROBES="$4"
mkdir -p "$PROBES"
MVK="$RUNTIME/lib/libMoltenVK.dylib"
[ -f "$MVK" ] || { echo "Missing bundled MoltenVK: $MVK" >&2; exit 1; }
lipo -verify_arch x86_64 "$MVK"
clang -arch x86_64 -O2 -Wall -Wextra -Werror -Wno-ignored-attributes \
    -DWINE_UNIX_LIB -I"$WINE_SOURCE/include" "$ROOT/packaging/probes/vulkan.c" \
    -o "$PROBES/vulkan-native"
# Use the shipped library explicitly; a developer's Vulkan SDK must not satisfy this.
env -u DYLD_LIBRARY_PATH -u DYLD_FALLBACK_LIBRARY_PATH \
    python3 - "$PROBES/vulkan-native" "$MVK" <<'PY'
import os
import signal
import subprocess
import sys

process = subprocess.Popen(sys.argv[1:], start_new_session=True)
try:
    sys.exit(process.wait(timeout=30))
except subprocess.TimeoutExpired:
    os.killpg(process.pid, signal.SIGKILL)
    process.wait()
    sys.exit('Native Vulkan loader check timed out and was stopped.')
PY
for arch in x86_64 i686; do
    "$LLVM_MINGW/bin/$arch-w64-mingw32-clang" -O2 -Wall -Wextra -Werror \
        -I"$WINE_SOURCE/include" "$ROOT/packaging/probes/vulkan.c" \
        -o "$PROBES/vulkan-$arch.exe"
done
echo "Native Vulkan loader check passed; Windows x86_64 and i386 probes compiled."
