#!/bin/bash
# Build Gluon's Windows runtime from published open-source code.
#
# Everything is either compiled here from source or taken from an open-source project's
# own release. Nothing is taken out of a commercial product. Every input is pinned by
# SHA-256.
#
#   Wine          Wine 11.0 with CodeWeavers' macOS and Rosetta fixes, built from their
#                 open-source release (LGPL-2.1)
#   FreeType      built from the same source release
#   MoltenVK      KhronosGroup release (Apache-2.0)
#   DXMT          3Shain/dxmt release (LGPL-2.1) - Direct3D 10/11 on Metal
#   Steam bridge  optional input: build/inputs/steam-bridge-*.tar.gz
#
# Runs on an Apple Silicon Mac with Rosetta 2, Xcode's command line tools, and Homebrew
# bison, flex, autoconf, automake, libtool, pkgconf.
#
# Usage: packaging/build-runtime.sh
# Env:   WORK  build folder (default <repo>/build)
#        OUT   finished runtime (default $WORK/gluon-wine)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${WORK:-$ROOT/build}"
OUT="${OUT:-$WORK/gluon-wine}"
DL="$WORK/downloads"
SRC="$WORK/src"
DEPS="$WORK/deps"
JOBS="$(sysctl -n hw.ncpu)"

CX_SRC_URL="https://media.codeweavers.com/pub/crossover/source/crossover-sources-26.3.0.tar.gz"
CX_SRC_SHA="ac99c8ca4b3848f3e81784135f023df266b61c2345726ea55a50b3e030dd6872"
LLVM_MINGW_URL="https://github.com/mstorsjo/llvm-mingw/releases/download/20261006/llvm-mingw-20261006-ucrt-macos-universal.tar.xz"
LLVM_MINGW_SHA="a7a19b11048e89372637d26279cdb7c430ed3cf8c1e224310421312bf986ebf6"
MOLTENVK_URL="https://github.com/KhronosGroup/MoltenVK/releases/download/v1.4.2/MoltenVK-macos.tar"
MOLTENVK_SHA="f95765a6229cb7b915990a2890ce12ebe36a730b021545d3d52ae69ce4c4024e"
DXMT_URL="https://github.com/3Shain/dxmt/releases/download/v0.80/dxmt-v0.80-builtin.tar.gz"
DXMT_SHA="8f260e36b5739e68f3bad613381441385c4dc7b85b78ba8de653d5a6a264529d"

log() { printf '\n==> %s\n' "$*"; }

fetch() { # url sha256 file
    if [ ! -f "$3" ] || ! echo "$2  $3" | shasum -a 256 -c - >/dev/null 2>&1; then
        curl -fL --retry 3 --retry-delay 5 -o "$3" "$1"
    fi
    echo "$2  $3" | shasum -a 256 -c -
}

mkdir -p "$DL" "$SRC" "$DEPS/lib" "$DEPS/include"

log "Fetching inputs"
fetch "$CX_SRC_URL" "$CX_SRC_SHA" "$DL/crossover-sources.tar.gz"
fetch "$LLVM_MINGW_URL" "$LLVM_MINGW_SHA" "$DL/llvm-mingw.tar.xz"
fetch "$MOLTENVK_URL" "$MOLTENVK_SHA" "$DL/MoltenVK-macos.tar"
fetch "$DXMT_URL" "$DXMT_SHA" "$DL/dxmt-builtin.tar.gz"

if [ ! -d "$SRC/wine" ]; then
    log "Unpacking Wine and FreeType sources"
    tar -xzf "$DL/crossover-sources.tar.gz" -C "$SRC" --strip-components 1 sources/wine sources/freetype
fi
if [ ! -d "$WORK/llvm-mingw" ]; then
    log "Unpacking llvm-mingw"
    mkdir -p "$WORK/llvm-mingw"
    tar -xJf "$DL/llvm-mingw.tar.xz" -C "$WORK/llvm-mingw" --strip-components 1
fi

# Homebrew's bison and flex first (the system ones are too old for Wine); the PE
# toolchain last so its clang never replaces Apple's for the macOS side.
for tool in bison flex; do
    if p="$(brew --prefix "$tool" 2>/dev/null)" && [ -d "$p/bin" ]; then PATH="$p/bin:$PATH"; fi
done
export PATH="$PATH:$WORK/llvm-mingw/bin"
export MACOSX_DEPLOYMENT_TARGET=11.0
# Build for x86_64 (the Windows side runs under Rosetta 2). Declaring build == host
# keeps this an ordinary native build; x86_64 helper programs run through Rosetta.
ARCH_FLAGS=(--build=x86_64-apple-darwin --host=x86_64-apple-darwin)
export CC="clang -arch x86_64" CXX="clang++ -arch x86_64"
export DYLD_FALLBACK_LIBRARY_PATH="$DEPS/lib:/usr/local/lib:/usr/lib"

if [ ! -f "$DEPS/lib/libfreetype.6.dylib" ]; then
    log "Building FreeType"
    cd "$SRC/freetype"
    [ -f builds/unix/configure ] || ./autogen.sh
    ./configure "${ARCH_FLAGS[@]}" --prefix="$DEPS" --enable-shared --disable-static \
        --with-zlib=yes --with-bzip2=no --with-png=no --with-harfbuzz=no --with-brotli=no
    make -j"$JOBS"
    make install
    install_name_tool -id @rpath/libfreetype.6.dylib "$DEPS/lib/libfreetype.6.dylib"
fi

if [ ! -f "$DEPS/lib/libMoltenVK.dylib" ]; then
    log "Unpacking MoltenVK"
    mkdir -p "$WORK/moltenvk"
    tar -xf "$DL/MoltenVK-macos.tar" -C "$WORK/moltenvk"
    mvk="$(find "$WORK/moltenvk" -type d -path '*MoltenVK/dylib/macOS' | head -1)"
    inc="$(find "$WORK/moltenvk" -type d -path '*MoltenVK/include' | head -1)"
    cp "$mvk/libMoltenVK.dylib" "$DEPS/lib/"
    cp -R "$inc"/* "$DEPS/include/"
    cp "$(find "$WORK/moltenvk" -maxdepth 2 -name LICENSE | head -1)" "$DEPS/MoltenVK-LICENSE.txt"
fi

if [ ! -x "$OUT/bin/wine" ]; then
    log "Configuring Wine"
    mkdir -p "$WORK/wine-build"
    cd "$WORK/wine-build"
    CFLAGS="-O2" CROSSCFLAGS="-O2" \
    CPPFLAGS="-I$DEPS/include" \
    LDFLAGS="-L$DEPS/lib -Wl,-rpath,@loader_path/../../" \
    FREETYPE_CFLAGS="-I$DEPS/include/freetype2" FREETYPE_LIBS="-L$DEPS/lib -lfreetype" \
    "$SRC/wine/configure" "${ARCH_FLAGS[@]}" --prefix="$OUT" \
        --enable-archs=i386,x86_64 --disable-tests \
        --with-mingw --with-freetype --with-vulkan --with-coreaudio --with-opengl \
        --without-x --without-wayland --without-alsa --without-pulse --without-oss \
        --without-dbus --without-udev --without-usb --without-v4l2 --without-sane \
        --without-gphoto --without-capi --without-netapi --without-krb5 --without-gssapi \
        --without-fontconfig --without-gettextpo --without-inotify \
        --without-gnutls --without-gstreamer --without-sdl --without-cups --without-pcap
    log "Building Wine"
    make -j"$JOBS"
    log "Installing Wine"
    make install
fi

log "Adding support libraries and graphics layers"
cp -f "$DEPS/lib/libfreetype.6.dylib" "$DEPS/lib/libMoltenVK.dylib" "$OUT/lib/"

dxmt="$WORK/dxmt"
rm -rf "$dxmt" && mkdir -p "$dxmt"
tar -xzf "$DL/dxmt-builtin.tar.gz" -C "$dxmt" --strip-components 1
for arch in x86_64-windows i386-windows; do
    for f in d3d11.dll dxgi.dll d3d10core.dll; do
        # keep Wine's own implementation beside the replacement
        [ -f "$OUT/lib/wine/$arch/$f" ] && [ ! -f "$OUT/lib/wine/$arch/$f.wined3d" ] \
            && mv "$OUT/lib/wine/$arch/$f" "$OUT/lib/wine/$arch/$f.wined3d"
        cp "$dxmt/$arch/$f" "$OUT/lib/wine/$arch/$f"
    done
    cp "$dxmt/$arch/winemetal.dll" "$OUT/lib/wine/$arch/"
done
cp "$dxmt/x86_64-unix/winemetal.so" "$OUT/lib/wine/x86_64-unix/"

bridge="$(ls "$WORK"/inputs/steam-bridge-*.tar.gz 2>/dev/null | head -1 || true)"
if [ -n "$bridge" ]; then
    log "Adding the Steam API bridge from $(basename "$bridge")"
    tar -xzf "$bridge" -C "$OUT"
else
    log "No Steam API bridge input; games will not see the Steam client"
fi

log "Writing license texts and the component list"
L="$OUT/licenses"
mkdir -p "$L"
cp "$SRC/wine/COPYING.LIB" "$L/Wine-COPYING.LIB.txt"
cp "$SRC/wine/LICENSE" "$L/Wine-LICENSE.txt"
cp "$SRC/freetype/LICENSE.TXT" "$L/FreeType-LICENSE.txt" 2>/dev/null || cp "$SRC/freetype/docs/FTL.TXT" "$L/FreeType-LICENSE.txt"
cp "$DEPS/MoltenVK-LICENSE.txt" "$L/"
curl -fsSL https://raw.githubusercontent.com/3Shain/dxmt/v0.80/LICENSE -o "$L/DXMT-LICENSE.txt" || true
cat > "$L/SOURCES.txt" <<EOF
Source and origin of every component in this runtime

Wine 11.0 with CodeWeavers' changes - compiled by Gluon's build from
  $CX_SRC_URL
  sha256 $CX_SRC_SHA
FreeType - compiled by Gluon's build from the same archive
MoltenVK - $MOLTENVK_URL
DXMT - $DXMT_URL  (source: https://github.com/3Shain/dxmt, tag v0.80)
Steam API bridge, when present - see licenses/steam-bridge/
Build script: packaging/build-runtime.sh in the Gluon repository
EOF
cat > "$OUT/GLUON-COMPONENTS.txt" <<EOF
Gluon Windows runtime, built $(date -u +%F) by packaging/build-runtime.sh.
Compiled from source: Wine 11.0 with CodeWeavers' open-source changes, FreeType.
From upstream releases: MoltenVK v1.4.2, DXMT v0.80 (installed over d3d11, dxgi, d3d10core;
Wine's originals kept as *.wined3d).
Not included: wine-mono and Gecko, GnuTLS, GStreamer, SDL.
See licenses/SOURCES.txt.
EOF

log "Checking the result"
file "$OUT/bin/wine" | sed "s#$OUT/##"
"$OUT/bin/wine" --version
du -sh "$OUT" | cut -f1
log "Runtime ready: $OUT"
