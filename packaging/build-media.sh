#!/bin/bash
# Native x86_64 dependencies for Wine's GStreamer bridge and SDL controller bus.
# Build only on GitHub infrastructure; never copy a commercial runtime component.
# Usage: build-media.sh <verified crossover-source.tar.gz> <deps-prefix> <runtime>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$ROOT/packaging/require-cloud.sh"
[ "$(uname -s)" = Darwin ] || { echo 'Media dependencies require a macOS Actions runner' >&2; exit 1; }
ARCHIVE="${1:?verified Wine source archive required}"
DEPS="${2:?native dependency prefix required}"
OUT="${3:?runtime directory required}"
WORK="${WORK:-$ROOT/build}"
JOBS="${JOBS:-4}"
case "$JOBS" in ''|*[!0-9]*) echo 'JOBS must be an integer from 1 to 4' >&2; exit 1;; esac
[ "$JOBS" -ge 1 ] && [ "$JOBS" -le 4 ] || { echo 'Media builds use at most four workers' >&2; exit 1; }
DL="$WORK/downloads"
SRC="$WORK/media-src"
BUILD="$WORK/media-build"
TOOLS="$WORK/media-tools"
mkdir -p "$DL" "$SRC" "$BUILD" "$TOOLS" "$DEPS/lib" "$DEPS/include" "$OUT/licenses/media"
DEPS="$(cd "$DEPS" && pwd)"
OUT="$(cd "$OUT" && pwd)"
WORK="$(cd "$WORK" && pwd)"
echo 'ac99c8ca4b3848f3e81784135f023df266b61c2345726ea55a50b3e030dd6872  '"$ARCHIVE" | shasum -a 256 -c -

fetch() {
    if [ ! -f "$DL/$3" ] || ! echo "$2  $DL/$3" | shasum -a 256 -c - >/dev/null 2>&1; then
        curl -fL --retry 3 --retry-delay 5 -o "$DL/$3" "$1"
    fi
    echo "$2  $DL/$3" | shasum -a 256 -c -
}
fetch https://github.com/libsdl-org/SDL/releases/download/release-2.32.10/SDL2-2.32.10.tar.gz \
    5f5993c530f084535c65a6879e9b26ad441169b3e25d789d83287040a9ca5165 SDL2-2.32.10.tar.gz
fetch https://github.com/libffi/libffi/releases/download/v3.4.6/libffi-3.4.6.tar.gz \
    b0dea9df23c863a7a50e825440f3ebffabd65df1497108e5d437747843895a4e libffi-3.4.6.tar.gz
fetch https://github.com/PhilipHazel/pcre2/releases/download/pcre2-10.42/pcre2-10.42.tar.bz2 \
    8d36cd8cb6ea2a4c2bb358ff6411b0c788633a2a45dabbf1aeb4b701d1b5e840 pcre2-10.42.tar.bz2
fetch https://codeload.github.com/frida/proxy-libintl/tar.gz/refs/tags/0.4 \
    13ef3eea0a3bc0df55293be368dfbcff5a8dd5f4759280f28e030d1494a5dffb proxy-libintl-0.4.tar.gz
# Match GLib 2.78.0's gvdb.wrap; the published archive leaves this submodule empty.
GVDB_REV=0854af0fdb6d527a8d1999835ac2c5059976c210
fetch "https://gitlab.gnome.org/GNOME/gvdb/-/archive/$GVDB_REV/gvdb-$GVDB_REV.tar.gz" \
    08352e54e8216d9001820c627c62858585465b51dc557cc22f0f4770ed182ebd "gvdb-$GVDB_REV.tar.gz"
fetch https://ffmpeg.org/releases/ffmpeg-6.1.6.tar.xz \
    d4fcb164028dd3beee5d92c0ac72e46aac6973c75ea12dc14de07bf8f407370a ffmpeg-6.1.6.tar.xz
fetch https://github.com/mesonbuild/meson/releases/download/1.12.1/meson-1.12.1.tar.gz \
    ab0a6ca09f8ef70c564c8241fb5a23957886a0b53fb58412b5e07eaf07dba743 meson-1.12.1.tar.gz
fetch https://github.com/ninja-build/ninja/releases/download/v1.13.2/ninja-mac.zip \
    c99048673aa765960a99cf10c6ddb9f1fad506099ff0a0e137ad8960a88f321b ninja-1.13.2-mac.zip
mkdir -p "$TOOLS/meson" "$TOOLS/ninja"
tar -xzf "$DL/meson-1.12.1.tar.gz" -C "$TOOLS/meson" --strip-components 1
unzip -oq "$DL/ninja-1.13.2-mac.zip" -d "$TOOLS/ninja"
export PATH="$TOOLS/ninja:$PATH"
MESON=(python3 "$TOOLS/meson/meson.py")
export CC='clang -arch x86_64' CXX='clang++ -arch x86_64'
export OBJC="$CC" OBJCXX="$CXX"
export CFLAGS='-O2 -fPIC' CXXFLAGS='-O2 -fPIC' OBJCFLAGS='-O2 -fPIC' OBJCXXFLAGS='-O2 -fPIC'
export CPPFLAGS="-I$DEPS/include" LDFLAGS="-L$DEPS/lib -Wl,-headerpad_max_install_names"
# No dependency may be satisfied by a Homebrew runtime dylib or wrap download.
export PKG_CONFIG_PATH= PKG_CONFIG_LIBDIR="$DEPS/lib/pkgconfig:$DEPS/share/pkgconfig"
unset GST_ENV
# Meson's native CPU detection starts with Python's platform.machine(); on an
# arm64 runner, changing clang's -arch alone leaves host_machine as aarch64.
# Use an explicit x86_64 host description. Rosetta is already required by Wine.
cat > "$BUILD/native-x86_64.ini" <<EOF
[binaries]
c = ['clang', '-arch', 'x86_64']
cpp = ['clang++', '-arch', 'x86_64']
objc = ['clang', '-arch', 'x86_64']
objcpp = ['clang++', '-arch', 'x86_64']
ar = 'ar'
strip = 'strip'
pkg-config = 'pkg-config'
[properties]
needs_exe_wrapper = false
pkg_config_libdir = ['$DEPS/lib/pkgconfig', '$DEPS/share/pkgconfig']
[host_machine]
system = 'darwin'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'
EOF

if [ ! -f "$SRC/glib/meson.build" ] || [ ! -f "$SRC/gstreamer/meson.build" ]; then
    tar -xzf "$ARCHIVE" -C "$SRC" --strip-components 1 sources/glib sources/gstreamer
fi
unpack() {
    mkdir -p "$SRC/$2"
    tar -xf "$DL/$1" -C "$SRC/$2" --strip-components 1
}
unpack SDL2-2.32.10.tar.gz sdl2
unpack libffi-3.4.6.tar.gz libffi
unpack pcre2-10.42.tar.bz2 pcre2
unpack ffmpeg-6.1.6.tar.xz ffmpeg
grep -qx "revision=$GVDB_REV" "$SRC/glib/subprojects/gvdb.wrap" || {
    echo 'GLib GVDB revision differs from the pinned source' >&2; exit 1;
}
unpack "gvdb-$GVDB_REV.tar.gz" glib/subprojects/gvdb
for file in meson.build COPYING gvdb/gvdb-builder.c gvdb/gvdb-builder.h gvdb/gvdb-reader.c gvdb/gvdb-reader.h gvdb/gvdb-format.h; do
    [ -s "$SRC/glib/subprojects/gvdb/$file" ] || {
        echo "Missing GVDB source: $file" >&2; exit 1;
    }
done
# GLib's archived wrap points at mutable Git branches. Supply the pinned source
# directory instead; Meson is forbidden to download any fallback.
mkdir -p "$SRC/glib/subprojects/proxy-libintl"
tar -xzf "$DL/proxy-libintl-0.4.tar.gz" -C "$SRC/glib/subprojects/proxy-libintl" --strip-components 1

for library in libffi pcre2; do
    mkdir -p "$BUILD/$library"
    (
        cd "$BUILD/$library"
        options=(--prefix="$DEPS" --host=x86_64-apple-darwin --disable-shared --enable-static)
        if [ "$library" = pcre2 ]; then
            options+=(--disable-pcre2-16 --disable-pcre2-32 --disable-jit)
        fi
        "$SRC/$library/configure" "${options[@]}"
        if [ "$library" = pcre2 ]; then
            make -j"$JOBS" libpcre2-8.la libpcre2-posix.la
            make install-libLTLIBRARIES install-includeHEADERS install-nodist_includeHEADERS install-pkgconfigDATA
        else
            make -j"$JOBS"
            make install
        fi
    )
done

meson_build() {
    project="$1"; shift
    args=(setup "$BUILD/$project" "$SRC/$project" --prefix "$DEPS" --libdir lib
        --cross-file "$BUILD/native-x86_64.ini"
        --buildtype release -Doptimization=2 --wrap-mode=nodownload -Dauto_features=disabled)
    [ ! -f "$BUILD/$project/meson-private/coredata.dat" ] || args+=(--reconfigure)
    "${MESON[@]}" "${args[@]}" "$@"
    ninja -C "$BUILD/$project" -j"$JOBS"
    "${MESON[@]}" install -C "$BUILD/$project" --no-rebuild
}
meson_build glib -Dtests=false -Dinstalled_tests=false -Dgtk_doc=false -Dnls=disabled \
    -Dlibmount=disabled -Dsysprof=disabled -Dlibelf=disabled

# Software decoders and standard Apple system interfaces only. No optional
# GPL/nonfree codec library or runner-specific autodetected dependency is linked.
mkdir -p "$BUILD/ffmpeg"
(
    cd "$BUILD/ffmpeg"
    "$SRC/ffmpeg/configure" --prefix="$DEPS" --arch=x86_64 --target-os=darwin \
        --cc='clang -arch x86_64' --cxx='clang++ -arch x86_64' \
        --extra-cflags='-O2' --extra-ldflags="-L$DEPS/lib -Wl,-headerpad_max_install_names" \
        --enable-shared --disable-static --disable-programs --disable-doc --disable-debug \
        --disable-autodetect --disable-gpl --disable-nonfree --disable-version3 \
        --disable-x86asm --enable-pic
    make -j"$JOBS"
    make install
)

# Explicit plugins: avoid silently varying codec/controller support between CI
# runners. CGL integration here serves Apple-media buffers; no GL probe is run.
gst_options=(-Dbase=enabled -Dgood=enabled -Dbad=enabled -Dugly=enabled -Dlibav=enabled
    -Dbuild-tools-source=system -Dorc=disabled -Dtests=disabled -Dexamples=disabled
    -Dintrospection=disabled -Dnls=disabled -Dgpl=disabled -Dtools=disabled
    -Dgstreamer:tools=enabled -Dgstreamer:check=disabled
    -Dgst-plugins-base:gl=enabled -Dgst-plugins-base:gl_api=opengl
    -Dgst-plugins-base:gl_platform=cgl -Dgst-plugins-base:gl_winsys=cocoa
    -Dgst-plugins-bad:gl=enabled -Dgst-plugins-bad:applemedia=enabled
    -Dgst-plugins-ugly:asfdemux=enabled)
for plugin in app audioconvert audiomixer audiorate audioresample encoding gio pbtypes playback rawparse subparse typefind videoconvertscale videorate volume; do
    gst_options+=("-Dgst-plugins-base:$plugin=enabled")
done
for plugin in alpha apetag audiofx audioparsers auparse autodetect avi deinterlace flv icydemux id3demux imagefreeze interleave isomp4 law level matroska multifile multipart videobox videocrop videofilter wavparse; do
    gst_options+=("-Dgst-plugins-good:$plugin=enabled")
done
for plugin in adpcmdec aiff autoconvert codectimestamper ivfparse jpegformat mpegdemux mpegtsdemux pnm rawparse videoparsers vmnc; do
    gst_options+=("-Dgst-plugins-bad:$plugin=enabled")
done
meson_build gstreamer "${gst_options[@]}"

cmake -S "$SRC/sdl2" -B "$BUILD/sdl2" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_OSX_ARCHITECTURES=x86_64 -DCMAKE_INSTALL_PREFIX="$DEPS" \
    -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_C_FLAGS_RELEASE='-O2 -DNDEBUG' \
    -DSDL_SHARED=ON -DSDL_STATIC=OFF -DSDL_TEST=OFF -DSDL_TESTS=OFF \
    -DSDL_INSTALL_TESTS=OFF -DSDL_AUDIO=OFF -DSDL_VIDEO=OFF -DSDL_RENDER=OFF
cmake --build "$BUILD/sdl2" --parallel "$JOBS"
cmake --install "$BUILD/sdl2"

python3 "$ROOT/packaging/relocate-media.py" "$DEPS" "$OUT"
NOTICES="$OUT/licenses/media"
cp "$SRC/sdl2/LICENSE.txt" "$NOTICES/SDL2-LICENSE.txt"
cp "$SRC/libffi/LICENSE" "$NOTICES/libffi-LICENSE.txt"
cp "$SRC/pcre2/LICENCE" "$NOTICES/PCRE2-LICENSE.txt"
cp "$SRC/glib/COPYING" "$NOTICES/GLib-COPYING.txt"
cp -R "$SRC/glib/LICENSES" "$NOTICES/GLib-LICENSES"
cp "$SRC/glib/subprojects/gvdb/COPYING" "$NOTICES/GVDB-COPYING.txt"
cp "$SRC/glib/subprojects/proxy-libintl/COPYING" "$NOTICES/proxy-libintl-COPYING.txt"
cp "$SRC/gstreamer/LICENSE" "$NOTICES/GStreamer-LICENSE.txt"
for component in gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugins-ugly gst-libav; do
    cp "$SRC/gstreamer/subprojects/$component/COPYING" "$NOTICES/$component-COPYING.txt"
done
cp "$SRC/ffmpeg/COPYING.LGPLv2.1" "$NOTICES/FFmpeg-COPYING.LGPLv2.1.txt"
cp "$SRC/ffmpeg/LICENSE.md" "$NOTICES/FFmpeg-LICENSE.md"
cp "$BUILD/ffmpeg/config.h" "$NOTICES/FFmpeg-config.h"
cp "$ROOT/packaging/build-media.sh" "$NOTICES/Gluon-build-media.sh"
cp "$ROOT/packaging/relocate-media.py" "$NOTICES/Gluon-relocate-media.py"
cat > "$NOTICES/SOURCES.txt" <<'EOF'
GLib 2.78.0 and GStreamer 1.24.4 (core, base/good/bad/ugly/libav):
https://media.codeweavers.com/pub/crossover/source/crossover-sources-26.3.0.tar.gz
SHA-256 ac99c8ca4b3848f3e81784135f023df266b61c2345726ea55a50b3e030dd6872
GVDB, GLib 2.78.0's pinned submodule commit 0854af0fdb6d527a8d1999835ac2c5059976c210:
https://gitlab.gnome.org/GNOME/gvdb/-/archive/0854af0fdb6d527a8d1999835ac2c5059976c210/gvdb-0854af0fdb6d527a8d1999835ac2c5059976c210.tar.gz
SHA-256 08352e54e8216d9001820c627c62858585465b51dc557cc22f0f4770ed182ebd
SDL2 2.32.10: https://github.com/libsdl-org/SDL/releases/tag/release-2.32.10
SHA-256 5f5993c530f084535c65a6879e9b26ad441169b3e25d789d83287040a9ca5165
libffi 3.4.6: https://github.com/libffi/libffi/releases/tag/v3.4.6
SHA-256 b0dea9df23c863a7a50e825440f3ebffabd65df1497108e5d437747843895a4e
PCRE2 10.42: https://github.com/PhilipHazel/pcre2/releases/tag/pcre2-10.42
SHA-256 8d36cd8cb6ea2a4c2bb358ff6411b0c788633a2a45dabbf1aeb4b701d1b5e840
proxy-libintl 0.4, commit c03e1a74b17fa7ec467e110130775409e4828a4c:
https://github.com/frida/proxy-libintl/tree/0.4
SHA-256 13ef3eea0a3bc0df55293be368dfbcff5a8dd5f4759280f28e030d1494a5dffb
FFmpeg 6.1.6: https://ffmpeg.org/releases/ffmpeg-6.1.6.tar.xz
SHA-256 d4fcb164028dd3beee5d92c0ac72e46aac6973c75ea12dc14de07bf8f407370a
FFmpeg uses LGPL configuration, without GPL/nonfree/version3 or external codecs.
libffi and PCRE2 are linked into GLib; proxy-libintl is a shared NLS stub library.
GVDB reader/builder sources are compiled into GLib and its resource/schema tools.
All native code is compiled from these sources for x86_64, -O2, on macOS Actions.
No native components are copied from a commercial application or Homebrew.
See the accompanying build helpers for exact plugin options and transformations.
EOF
printf '%s\n' 'glib-2.78.0 gst-1.24.4 ffmpeg-6.1.6 sdl-2.32.10' > "$OUT/.gluon-media"
echo 'Source-built SDL and GStreamer media dependencies installed.'
