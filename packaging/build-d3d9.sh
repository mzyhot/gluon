#!/bin/bash
# Build only Direct3D 9 from the DXVK source in CodeWeavers' published archive.
# No DLL from a commercial product is used. The runtime builder verifies that
# archive before extraction; this helper pins its additional build tools too.
#
# Usage: packaging/build-d3d9.sh <dxvk-source> <llvm-mingw> <runtime> [--build-only]
# WORK and JOBS may be set by the caller. --build-only leaves DLLs in WORK/d3d9.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$ROOT/packaging/require-cloud.sh"
WORK="${WORK:-$ROOT/build}"
JOBS="${JOBS:-4}"
DXVK_SRC="${1:?DXVK source directory required}"
MINGW="${2:?llvm-mingw directory required}"
OUT="${3:?runtime directory required}"
BUILD_ONLY="${4:-}"
[ -z "$BUILD_ONLY" ] || [ "$BUILD_ONLY" = --build-only ] || { echo "Unknown option: $BUILD_ONLY" >&2; exit 1; }

DL="$WORK/downloads"
TOOLS="$WORK/d3d9-tools"
BUILD="$WORK/d3d9"
MESON_URL="https://github.com/mesonbuild/meson/releases/download/1.12.1/meson-1.12.1.tar.gz"
MESON_SHA="ab0a6ca09f8ef70c564c8241fb5a23957886a0b53fb58412b5e07eaf07dba743"
GLSLANG_URL="https://github.com/KhronosGroup/glslang/releases/download/16.6.0/glslang-16.6.0-macos-universal-release.tar.gz"
GLSLANG_SHA="4f8c05f2888a73ed0b7b274bc524af9130e09ae94b43c0f3f9ed83cfae852834"
NINJA_URL="https://github.com/ninja-build/ninja/releases/download/v1.13.2/ninja-mac.zip"
NINJA_SHA="c99048673aa765960a99cf10c6ddb9f1fad506099ff0a0e137ad8960a88f321b"

fetch() {
    if [ ! -f "$3" ] || ! echo "$2  $3" | shasum -a 256 -c - >/dev/null 2>&1; then
        curl -fL --retry 3 --retry-delay 5 -o "$3" "$1"
    fi
    echo "$2  $3" | shasum -a 256 -c -
}

[ -f "$DXVK_SRC/meson.build" ] && [ -f "$DXVK_SRC/LICENSE" ] || { echo "DXVK source is incomplete" >&2; exit 1; }
if ! grep -q '^#include <algorithm>' "$DXVK_SRC/src/util/config/config.cpp"; then
    patch -d "$DXVK_SRC" -p1 < "$ROOT/packaging/patches/dxvk-explicit-algorithm.patch"
fi
if grep -q '^typedef struct _D3DDEVINFO_RESOURCEMANAGER' "$DXVK_SRC/src/d3d9/d3d9_include.h"; then
    patch -d "$DXVK_SRC" -p1 < "$ROOT/packaging/patches/dxvk-mingw-resource-manager.patch"
fi
if grep -q 'enabled.core.features.geometryShader = VK_TRUE;' "$DXVK_SRC/src/d3d9/d3d9_device.cpp"; then
    patch -d "$DXVK_SRC" -p1 < "$ROOT/packaging/patches/dxvk-supported-metal-features.patch"
fi
for arch in x86_64 i686; do
    [ -x "$MINGW/bin/$arch-w64-mingw32-clang++" ] || { echo "Missing llvm-mingw $arch compiler" >&2; exit 1; }
done
if [ -z "$BUILD_ONLY" ]; then
    for arch in x86_64-windows i386-windows; do
        [ -f "$OUT/lib/wine/$arch/d3d9.dll" ] || { echo "Wine's $arch d3d9.dll must be installed first" >&2; exit 1; }
    done
fi

mkdir -p "$DL" "$TOOLS/meson" "$TOOLS/glslang" "$TOOLS/ninja" "$BUILD"
fetch "$MESON_URL" "$MESON_SHA" "$DL/meson-1.12.1.tar.gz"
fetch "$GLSLANG_URL" "$GLSLANG_SHA" "$DL/glslang-16.6.0-macos-universal-release.tar.gz"
fetch "$NINJA_URL" "$NINJA_SHA" "$DL/ninja-1.13.2-mac.zip"
tar -xzf "$DL/meson-1.12.1.tar.gz" -C "$TOOLS/meson" --strip-components 1
tar -xzf "$DL/glslang-16.6.0-macos-universal-release.tar.gz" -C "$TOOLS/glslang"
unzip -oq "$DL/ninja-1.13.2-mac.zip" -d "$TOOLS/ninja"
export PATH="$TOOLS/ninja:$TOOLS/glslang/bin:$MINGW/bin:$PATH"
# The parent Wine build exports Apple clang flags and macOS headers/libraries.
# They must never leak into the Windows cross build or its native generators.
unset CC CXX CFLAGS CXXFLAGS CPPFLAGS LDFLAGS

for arch in x86_64 i386; do
    triple=x86_64-w64-mingw32
    family=x86_64
    machine=0x8664
    if [ "$arch" = i386 ]; then triple=i686-w64-mingw32; family=x86; machine=0x014c; fi
    cross="$BUILD/$arch.txt"
    cat > "$cross" <<EOF
[binaries]
c = '$triple-clang'
cpp = '$triple-clang++'
ar = '$triple-ar'
strip = '$triple-strip'
windres = '$triple-windres'
[properties]
needs_exe_wrapper = true
[host_machine]
system = 'windows'
cpu_family = '$family'
cpu = '$arch'
endian = 'little'
EOF
    args=(setup "$BUILD/$arch" "$DXVK_SRC" --cross-file "$cross"
        --buildtype release --strip -Denable_d3d9=true -Denable_dxgi=false
        -Denable_d3d10=false -Denable_d3d11=false -Denable_tests=false)
    [ ! -f "$BUILD/$arch/meson-private/coredata.dat" ] || args+=(--reconfigure)
    python3 "$TOOLS/meson/meson.py" "${args[@]}"
    ninja -C "$BUILD/$arch" -j"$JOBS" src/d3d9/d3d9.dll
    dll="$BUILD/$arch/src/d3d9/d3d9.dll"
    "$MINGW/bin/$triple-strip" "$dll"
    # Wine ignores an unmarked native DLL in its built-in search directory.
    # Reserve the DOS stub for the same marker used by Wine and DXMT's builtin
    # release; guard against overlapping the actual PE header before writing it.
    python3 - "$dll" "$machine" <<'PY'
import pathlib
import struct
import sys
path = pathlib.Path(sys.argv[1])
data = bytearray(path.read_bytes())
assert data[:2] == b'MZ', f'{path}: not a PE DLL'
pe = struct.unpack_from('<I', data, 0x3c)[0]
assert pe >= 0x60 and data[pe:pe + 4] == b'PE\0\0', f'{path}: invalid PE header or DOS stub'
machine, = struct.unpack_from('<H', data, pe + 4)
assert machine == int(sys.argv[2], 0), f'{path}: wrong PE machine {machine:#x}'
flags, = struct.unpack_from('<H', data, pe + 22)
assert flags & 0x2000, f'{path}: PE image is not a DLL'
data[0x40:0x60] = b'Wine builtin DLL'.ljust(32, b'\0')
path.write_bytes(data)
print(f'{path}: PE machine {machine:#x}; Wine builtin marker installed')
PY
    "$MINGW/bin/llvm-readobj" --coff-imports --coff-exports "$dll" > "$BUILD/$arch/pe-inspection.txt"
    for symbol in Direct3DCreate9 Direct3DCreate9Ex; do
        grep -q "Name: $symbol$" "$BUILD/$arch/pe-inspection.txt" || { echo "$dll: missing $symbol export" >&2; exit 1; }
    done
    grep -iq 'Name: vulkan-1.dll$' "$BUILD/$arch/pe-inspection.txt" || { echo "$dll: missing Vulkan loader import" >&2; exit 1; }
    if grep -Ei 'Name: (libc\+\+|libstdc\+\+|libgcc|libwinpthread).*\.dll$' "$BUILD/$arch/pe-inspection.txt"; then
        echo "$dll: unexpected external compiler runtime dependency" >&2; exit 1
    fi
done

# Both architectures must have passed before replacing either installed DLL.
if [ -z "$BUILD_ONLY" ]; then
    for arch in x86_64 i386; do
        dest="$OUT/lib/wine/$arch-windows/d3d9.dll"
        [ -f "$dest.wined3d" ] || cp -p "$dest" "$dest.wined3d"
        cp "$BUILD/$arch/src/d3d9/d3d9.dll" "$dest"
    done
    mkdir -p "$OUT/licenses"
    cp "$DXVK_SRC/LICENSE" "$OUT/licenses/DXVK-LICENSE.txt"
    cp "$DXVK_SRC/include/openvr/LICENSE" "$OUT/licenses/DXVK-OpenVR-LICENSE.txt"
    cp "$MINGW/LICENSE.TXT" "$OUT/licenses/DXVK-LLVM-LICENSE.txt"
    cp "$MINGW/x86_64-w64-mingw32/share/mingw32/COPYING.MinGW-w64-runtime.txt" "$OUT/licenses/DXVK-MinGW-runtime-LICENSE.txt"
    # These header-only dependencies carry their license notices in the headers.
    sed -n '1,28p' "$DXVK_SRC/include/spirv/spirv.hpp" > "$OUT/licenses/DXVK-SPIRV-Headers-LICENSE.txt"
    cat > "$OUT/licenses/DXVK-BUILD.txt" <<EOF
DXVK 1.10.3 from sources/dxvk in CodeWeavers' crossover-sources-26.3.0 archive.
Built from source for x86_64 and i386 by packaging/build-d3d9.sh.
Gluon source change: explicit <algorithm> include for std::transform, required by
  current libc++ (packaging/patches/dxvk-explicit-algorithm.patch).
Gluon source change: remove obsolete dummy D3DDEVINFO_RESOURCEMANAGER declaration;
  pinned MinGW-w64 supplies the proper type (dxvk-mingw-resource-manager.patch).
Gluon source change: negotiate geometryShader, shaderCullDistance and
  fillModeNonSolid instead of unconditionally requesting unsupported Metal
  features (dxvk-supported-metal-features.patch). Based on Sikarugir d9vk:
  https://github.com/Sikarugir-App/d9vk/blob/f229921c1b7f14edeadeccff04a9e65d0a016148/src/d3d9/d3d9_device.cpp
Only d3d9.dll is installed; Direct3D 10/11 and DXGI remain provided by DXMT.
Gluon adds Wine's built-in DOS-stub marker to each source-built DLL.
Wine's Direct3D 9 originals are preserved as d3d9.dll.wined3d.
Build-only dependencies (not installed in the runtime):
  Meson: $MESON_URL
    sha256 $MESON_SHA
  glslang: $GLSLANG_URL
    sha256 $GLSLANG_SHA
  Ninja: $NINJA_URL
    sha256 $NINJA_SHA
LLVM runtime license: DXVK-LLVM-LICENSE.txt; toolchain source:
  https://github.com/mstorsjo/llvm-mingw/tree/20261006
MinGW-w64 runtime notices: DXVK-MinGW-runtime-LICENSE.txt.
Vulkan header license: Apache-2.0 (see MoltenVK-LICENSE.txt).
SPIR-V header notice: DXVK-SPIRV-Headers-LICENSE.txt.
OpenVR header license: DXVK-OpenVR-LICENSE.txt.
EOF
fi

echo "Source-built D3D9 ready for x86_64 and i386: $BUILD"
