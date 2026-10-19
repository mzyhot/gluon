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
#   DXVK          built from the same source release (Zlib) - Direct3D 9 on Vulkan
#   Steam bridge  optional input: build/inputs/steam-bridge-*.tar.gz
#
# Runs on an Apple Silicon Mac with Rosetta 2, Xcode, CMake, and Homebrew bison and flex.
#
# Usage: packaging/build-runtime.sh
# Env:   WORK  build folder (default <repo>/build)
#        OUT   finished runtime (default $WORK/gluon-wine)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$ROOT/packaging/require-cloud.sh"
WORK="${WORK:-$ROOT/build}"
OUT="${OUT:-$WORK/gluon-wine}"
DL="$WORK/downloads"
SRC="$WORK/src"
DEPS="$WORK/deps"
JOBS="${JOBS:-4}"

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
if [ ! -f "$SRC/dxvk/meson.build" ]; then
    log "Unpacking Direct3D 9 sources"
    tar -xzf "$DL/crossover-sources.tar.gz" -C "$SRC" --strip-components 1 sources/dxvk
fi

log "Applying Wine OpenGL and Vulkan changes"
"$ROOT/packaging/apply-wine-patches.sh" "$SRC/wine"
"$ROOT/packaging/check-opengl-aliases.sh" "$SRC/wine" "$DL"
"$ROOT/packaging/check-vulkan-portability.sh" "$SRC/wine" "$DL"
python3 "$ROOT/packaging/check-gl-compat-as-core.py" "$SRC/wine" --source-only --self-test
python3 "$ROOT/packaging/check-wine-app-bundle.py" "$SRC/wine" --source-only --self-test
python3 "$ROOT/packaging/check-wine-overlay-scope.py" "$SRC/wine" --source-only --self-test
python3 "$ROOT/packaging/check-wine-font-ownership.py" "$SRC/wine" --source-only --self-test
WINE_PATCH_SHA="$("$ROOT/packaging/apply-wine-patches.sh" --hash)"
RUNTIME_BUILD_KEY="$(for helper in build-runtime.sh build-tls.sh build-media.sh build-addons.sh relocate-media.py extract-addon-notices.py; do
    printf '%s %s\n' "$helper" "$(shasum -a 256 "$ROOT/packaging/$helper" | cut -d' ' -f1)"
done | shasum -a 256 | cut -d' ' -f1)"
if [ -x "$OUT/bin/wine" ] && { [ ! -f "$OUT/.gluon-wine-patches" ] || [ "$(cat "$OUT/.gluon-wine-patches")" != "$WINE_PATCH_SHA" ]; }; then
    echo "Existing runtime predates the Wine patches. Build with a new WORK/OUT directory." >&2
    exit 1
fi
if [ -x "$OUT/bin/wine" ] && { [ ! -f "$OUT/.gluon-runtime-build-key" ] || [ "$(cat "$OUT/.gluon-runtime-build-key")" != "$RUNTIME_BUILD_KEY" ]; }; then
    echo "Existing runtime has different dependency/configuration inputs. Build with a new WORK/OUT directory." >&2
    exit 1
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
    # CMake build: the autotools entry point wants a git submodule the archive lacks.
    cmake -S "$SRC/freetype" -B "$WORK/freetype-build" \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES=x86_64 \
        -DCMAKE_OSX_DEPLOYMENT_TARGET="$MACOSX_DEPLOYMENT_TARGET" \
        -DCMAKE_INSTALL_PREFIX="$DEPS" -DCMAKE_INSTALL_NAME_DIR=@rpath \
        -DBUILD_SHARED_LIBS=ON -DFT_REQUIRE_ZLIB=ON \
        -DFT_DISABLE_HARFBUZZ=ON -DFT_DISABLE_PNG=ON -DFT_DISABLE_BZIP2=ON -DFT_DISABLE_BROTLI=ON
    cmake --build "$WORK/freetype-build" -j"$JOBS"
    cmake --install "$WORK/freetype-build"
    [ -f "$DEPS/lib/libfreetype.6.dylib" ] || { echo "FreeType did not produce libfreetype.6.dylib" >&2; ls "$DEPS/lib" >&2; exit 1; }
    otool -D "$DEPS/lib/libfreetype.6.dylib" | tail -1
fi

if [ ! -f "$DEPS/lib/libMoltenVK.dylib" ]; then
    log "Unpacking MoltenVK"
    mkdir -p "$WORK/moltenvk"
    tar -xf "$DL/MoltenVK-macos.tar" -C "$WORK/moltenvk"
    mvk="$(find "$WORK/moltenvk" -name libMoltenVK.dylib -path '*/dylib/macOS/*' | head -1)"
    inc="$(find "$WORK/moltenvk" -type d -name include -path '*MoltenVK/include' | head -1)"
    [ -n "$mvk" ] && [ -n "$inc" ] || { echo "unexpected MoltenVK archive layout" >&2; exit 1; }
    cp "$mvk" "$DEPS/lib/"
    lipo -info "$DEPS/lib/libMoltenVK.dylib"
    cp -R "$inc"/* "$DEPS/include/"
    cp "$(find "$WORK/moltenvk" -maxdepth 2 -name LICENSE | head -1)" "$DEPS/MoltenVK-LICENSE.txt"
fi

log "Building native TLS, media and controller dependencies"
WORK="$WORK" JOBS="$JOBS" "$ROOT/packaging/build-tls.sh" "$DL/crossover-sources.tar.gz" "$DEPS" "$OUT"
WORK="$WORK" JOBS="$JOBS" "$ROOT/packaging/build-media.sh" "$DL/crossover-sources.tar.gz" "$DEPS" "$OUT"
WORK="$WORK" "$ROOT/packaging/build-addons.sh" "$SRC/wine" "$OUT"
export PKG_CONFIG_LIBDIR="$DEPS/lib/pkgconfig:$DEPS/share/pkgconfig"
export PKG_CONFIG_PATH=''

if [ ! -x "$OUT/bin/wine" ]; then
    log "Configuring Wine"
    # Select the bundled implementation even if the runner has a Vulkan SDK installed.
    # Wine's cache variables accept the relocatable name used by its native bridge.
    export ac_cv_lib_soname_vulkan=''
    export ac_cv_lib_soname_MoltenVK='@rpath/libMoltenVK.dylib'
    export ac_cv_lib_soname_gnutls='@rpath/libgnutls.30.dylib'
    export ac_cv_lib_soname_SDL2='@rpath/libSDL2-2.0.0.dylib'
    # OpenGL is left to auto-detection: on macOS it comes from winemac.drv and the system
    # framework, and asking for it explicitly makes the (Linux-oriented) EGL check fatal.
    mkdir -p "$WORK/wine-build"
    cd "$WORK/wine-build"
    CFLAGS="-O2" CROSSCFLAGS="-O2" \
    CPPFLAGS="-I$DEPS/include" \
    LDFLAGS="-L$DEPS/lib -Wl,-rpath,@loader_path/../../" \
    FREETYPE_CFLAGS="-I$DEPS/include/freetype2" FREETYPE_LIBS="-L$DEPS/lib -lfreetype" \
    GNUTLS_CFLAGS="-I$DEPS/include" GNUTLS_LIBS="-L$DEPS/lib -lgnutls" \
    "$SRC/wine/configure" "${ARCH_FLAGS[@]}" --prefix="$OUT" \
        --enable-archs=i386,x86_64 --disable-tests \
        --with-mingw --with-freetype --with-vulkan --with-coreaudio \
        --with-gnutls --with-gstreamer --with-sdl \
        --without-x --without-wayland --without-alsa --without-pulse --without-oss \
        --without-dbus --without-udev --without-usb --without-v4l2 --without-sane \
        --without-gphoto --without-capi --without-netapi --without-krb5 --without-gssapi \
        --without-fontconfig --without-gettextpo --without-inotify \
        --without-cups --without-pcap
    log "Building Wine"
    make -j"$JOBS"
    log "Installing Wine"
    make install
    printf '%s\n' "$WINE_PATCH_SHA" > "$OUT/.gluon-wine-patches"
    printf '%s\n' "$RUNTIME_BUILD_KEY" > "$OUT/.gluon-runtime-build-key"
fi

log "Adding support libraries and graphics layers"
cp -fL "$DEPS/lib/libfreetype.6.dylib" "$DEPS/lib/libMoltenVK.dylib" "$OUT/lib/"

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

log "Building Direct3D 9 on Vulkan for x86_64 and i386"
WORK="$WORK" JOBS="$JOBS" "$ROOT/packaging/build-d3d9.sh" "$SRC/dxvk" "$WORK/llvm-mingw" "$OUT"

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
cp "$ROOT/packaging/patches/wine-opengl-promoted-aliases.patch" "$L/Gluon-Wine-OpenGL.patch"
cp "$ROOT/packaging/patches/wine-vulkan-portability.patch" "$L/Gluon-Wine-Vulkan.patch"
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
  Gluon modifications: licenses/Gluon-Wine-OpenGL.patch (LGPL-2.1-or-later)
  Native portability: licenses/Gluon-Wine-Vulkan.patch (LGPL-2.1-or-later)
FreeType - compiled by Gluon's build from the same archive
DXVK 1.10.3 (Direct3D 9 only) - compiled by Gluon's build from sources/dxvk
  in the same archive; Wine builtin marker added, see licenses/DXVK-BUILD.txt
MoltenVK - $MOLTENVK_URL
DXMT - $DXMT_URL  (source: https://github.com/3Shain/dxmt, tag v0.80)
Steam API bridge, when present - see licenses/steam-bridge/
TLS dependencies - see licenses/tls/
Media and SDL dependencies - see licenses/media/
Wine Mono and Gecko - see licenses/wine-addons/
Build script: packaging/build-runtime.sh in the Gluon repository
EOF
cat > "$OUT/GLUON-COMPONENTS.txt" <<EOF
Gluon Windows runtime, built $(date -u +%F) by packaging/build-runtime.sh.
Compiled from source: Wine 11.0 with CodeWeavers' open-source changes, FreeType,
DXVK 1.10.3 for Direct3D 9 on Vulkan (x86_64 and i386; d3d9.dll.wined3d preserved).
From upstream releases: MoltenVK v1.4.2, DXMT v0.80 (installed over d3d11, dxgi, d3d10core;
Wine's originals kept as *.wined3d).
Source-built TLS, GStreamer media and SDL controller dependencies are included;
see licenses/tls/ and licenses/media/ for complete source pins and licenses.
Wine Mono 10.4.1 and Gecko 2.47.4 are pinned official shared runtime releases;
see licenses/wine-addons/ for licenses and origins.
See licenses/SOURCES.txt.
EOF

log "Checking the result"
python3 "$ROOT/packaging/check-overlay-policy.py" "$OUT"
"$ROOT/packaging/check-vulkan.sh" "$OUT" "$SRC/wine" "$WORK/llvm-mingw" "$WORK/graphics-probes"
file "$OUT/bin/wine" | sed "s#$OUT/##"
"$OUT/bin/wine" --version
du -sh "$OUT" | cut -f1
log "Runtime ready: $OUT"
