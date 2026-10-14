#!/bin/bash
# Build Wine's TLS provider and its dependencies from pinned published sources.
# Usage: packaging/build-tls.sh <verified-CW-source-archive> <deps> <runtime>
# All compilation and the small native dependency smoke test run in GitHub only.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$ROOT/packaging/require-cloud.sh"
[ "$(uname -s)" = Darwin ] || { echo "TLS dependencies require a GitHub macOS host" >&2; exit 1; }
ARCHIVE="${1:?published source archive required}"
DEPS="${2:?dependency prefix required}"
OUT="${3:?runtime directory required}"
WORK="${WORK:-$ROOT/build}"
JOBS="${JOBS:-4}"
[[ "$JOBS" =~ ^[1-4]$ ]] || { echo "TLS JOBS must be between 1 and 4" >&2; exit 1; }
CX_SRC_SHA="ac99c8ca4b3848f3e81784135f023df266b61c2345726ea55a50b3e030dd6872"
NETTLE_URL="https://ftp.gnu.org/gnu/nettle/nettle-4.0.tar.gz"
NETTLE_SHA="3addbc00da01846b232fb3bc453538ea5468da43033f21bb345cb1e9073f5094"
GNUTLS_URL="https://www.gnupg.org/ftp/gcrypt/gnutls/v3.8/gnutls-3.8.13.tar.xz"
GNUTLS_SHA="ffed8ec1bf09c2426d4f14aae377de4753b53e537d685e604e99a8b16ca9c97e"

printf '%s  %s\n' "$CX_SRC_SHA" "$ARCHIVE" | shasum -a 256 -c -
command -v pkg-config >/dev/null || { echo "pkg-config is required for the TLS source build" >&2; exit 1; }
mkdir -p "$WORK/downloads" "$WORK/tls-src" "$WORK/tls-build" "$DEPS/lib" "$DEPS/include" "$OUT/lib"
ARCHIVE="$(cd "$(dirname "$ARCHIVE")" && pwd)/$(basename "$ARCHIVE")"
WORK="$(cd "$WORK" && pwd)"
DEPS="$(cd "$DEPS" && pwd)"
OUT="$(cd "$OUT" && pwd)"
DL="$WORK/downloads"
SRC="$WORK/tls-src"
BUILD="$WORK/tls-build"

fetch() {
    if [ ! -f "$3" ] || ! printf '%s  %s\n' "$2" "$3" | shasum -a 256 -c - >/dev/null 2>&1; then
        curl -fL --connect-timeout 15 --max-time 300 --retry 2 --retry-delay 5 -o "$3" "$1"
    fi
    printf '%s  %s\n' "$2" "$3" | shasum -a 256 -c -
}

# Avoid combining old dependency output with new source pins or build options.
BUILD_KEY="$(shasum -a 256 "$ROOT/packaging/build-tls.sh" | cut -d' ' -f1)"
if [ -f "$BUILD/source-key" ]; then
    [ "$(cat "$BUILD/source-key")" = "$BUILD_KEY" ] || { echo "TLS build inputs changed; use a fresh WORK/DEPS directory" >&2; exit 1; }
else
    for lib in libgnutls.30.dylib libnettle.9.dylib libhogweed.7.dylib libgmp.10.dylib; do
        [ ! -e "$DEPS/lib/$lib" ] || { echo "Unrecorded TLS dependency already exists: $DEPS/lib/$lib" >&2; exit 1; }
    done
    printf '%s\n' "$BUILD_KEY" > "$BUILD/source-key"
fi

fetch "$NETTLE_URL" "$NETTLE_SHA" "$DL/nettle-4.0.tar.gz"
fetch "$GNUTLS_URL" "$GNUTLS_SHA" "$DL/gnutls-3.8.13.tar.xz"
if [ ! -f "$SRC/gmp/configure" ]; then
    # The complete GMP source is in the verified open-source release, not an app.
    tar -xzf "$ARCHIVE" -C "$SRC" --strip-components 2 sources/gnutls/gmp
fi
if [ ! -f "$SRC/nettle/configure" ]; then
    # CW's Nettle directory lacks its top-level Makefile.in; use a complete release.
    mkdir -p "$SRC/nettle"
    tar -xzf "$DL/nettle-4.0.tar.gz" -C "$SRC/nettle" --strip-components 1
fi
if [ ! -f "$SRC/gnutls/configure" ]; then
    mkdir -p "$SRC/gnutls"
    tar -xJf "$DL/gnutls-3.8.13.tar.xz" -C "$SRC/gnutls" --strip-components 1
fi
for component in gmp nettle gnutls; do
    [ -f "$SRC/$component/configure" ] && [ -f "$SRC/$component/Makefile.in" ] || { echo "Incomplete $component source" >&2; exit 1; }
done
grep -q "PACKAGE_VERSION='6.3.0'" "$SRC/gmp/configure" || { echo "Unexpected published GMP version" >&2; exit 1; }
grep -q "PACKAGE_VERSION='4.0'" "$SRC/nettle/configure" || { echo "Unexpected Nettle version" >&2; exit 1; }
grep -q "PACKAGE_VERSION='3.8.13'" "$SRC/gnutls/configure" || { echo "Unexpected GnuTLS version" >&2; exit 1; }

export MACOSX_DEPLOYMENT_TARGET=11.0
export CC="/usr/bin/clang -arch x86_64" CXX="/usr/bin/clang++ -arch x86_64"
export CC_FOR_BUILD="$CC"
export CFLAGS=-O2 CXXFLAGS=-O2
export CPPFLAGS="-I$DEPS/include"
export LDFLAGS="-L$DEPS/lib -Wl,-headerpad_max_install_names -Wl,-rpath,@loader_path"
# A Homebrew arm64 dependency must never satisfy these x86_64 checks.
export PKG_CONFIG_LIBDIR="$DEPS/lib/pkgconfig"
export PKG_CONFIG_PATH=""
export DYLD_FALLBACK_LIBRARY_PATH="$DEPS/lib:/usr/lib"
ARCH_FLAGS=(--build=x86_64-apple-darwin --host=x86_64-apple-darwin --prefix="$DEPS")

mkdir -p "$BUILD/gmp" "$BUILD/nettle" "$BUILD/gnutls"
(
    cd "$BUILD/gmp"
    ABI=64 "$SRC/gmp/configure" "${ARCH_FLAGS[@]}" --enable-shared --disable-static --disable-cxx --with-pic --disable-maintainer-mode
    make -j"$JOBS"
    make install
)
(
    cd "$BUILD/nettle"
    "$SRC/nettle/configure" "${ARCH_FLAGS[@]}" --enable-shared --disable-static --disable-openssl --disable-documentation
    # Build/install the libraries and development headers without test/example tools.
    make -j"$JOBS" all-here
    make install-here
)
(
    cd "$BUILD/gnutls"
    "$SRC/gnutls/configure" "${ARCH_FLAGS[@]}" --enable-shared --disable-static \
        --disable-maintainer-mode --disable-doc --disable-tools --disable-tests --disable-cxx \
        --disable-nls --disable-rpath --disable-libdane --with-included-libtasn1 --with-included-unistring \
        --without-p11-kit --without-tpm --without-tpm2 --without-idn --without-zlib --without-brotli --without-zstd
    make -j"$JOBS"
    make install
)

# Keep the build-prefix dylibs usable by subsequent configure checks. Relocate only
# the copies shipped in the runtime, and reject dependencies outside this closed set.
for lib in libgnutls.30.dylib libnettle.9.dylib libhogweed.7.dylib libgmp.10.dylib; do
    [ -f "$DEPS/lib/$lib" ] || { echo "TLS library missing: $lib" >&2; exit 1; }
    cp -fL "$DEPS/lib/$lib" "$OUT/lib/$lib"
done
python3 - "$DEPS" "$OUT" <<'PY'
from pathlib import Path
import re
import subprocess
import sys

deps, runtime = map(Path, sys.argv[1:])
names = {'gnutls': 'libgnutls.30.dylib', 'nettle': 'libnettle.9.dylib',
         'hogweed': 'libhogweed.7.dylib', 'gmp': 'libgmp.10.dylib'}
for name in names.values():
    path = runtime / 'lib' / name
    subprocess.run(['lipo', str(path), '-verify_arch', 'x86_64'], check=True)
    lines = subprocess.check_output(['otool', '-L', str(path)], text=True).splitlines()[1:]
    for line in lines:
        dependency = line.strip().rsplit(' (compatibility version', 1)[0]
        if dependency.startswith(('/usr/lib/', '/System/Library/')):
            continue
        match = re.fullmatch(r'lib(gnutls|nettle|hogweed|gmp)(?:\.[0-9]+)*\.dylib', Path(dependency).name)
        if not match or not dependency.startswith(str(deps / 'lib') + '/'):
            raise SystemExit(f'{name}: unexpected native dependency {dependency}')
        subprocess.run(['install_name_tool', '-change', dependency,
                        '@rpath/' + names[match[1]], str(path)], check=True)
    subprocess.run(['install_name_tool', '-id', '@rpath/' + name, str(path)], check=True)
    load = subprocess.check_output(['otool', '-l', str(path)], text=True).splitlines()
    rpaths = []
    for i, line in enumerate(load):
        if line.strip() == 'cmd LC_RPATH':
            for candidate in load[i + 1:i + 5]:
                if candidate.strip().startswith('path '):
                    rpaths.append(candidate.strip()[5:].rsplit(' (offset ', 1)[0])
                    break
    for old in rpaths:
        if old != '@loader_path':
            subprocess.run(['install_name_tool', '-delete_rpath', old, str(path)], check=True)
    if '@loader_path' not in rpaths:
        subprocess.run(['install_name_tool', '-add_rpath', '@loader_path', str(path)], check=True)
    final = subprocess.check_output(['otool', '-L', str(path)], text=True)
    if str(deps) in final:
        raise SystemExit(f'{name}: dependency still refers to the build prefix')
    print(final.strip())
PY

# This loads the relocated runtime copies, not the build-prefix or Homebrew dylibs.
# It checks native init, randomness, SHA-256, TLS session and certificate credentials.
cat > "$BUILD/tls-check.c" <<'C'
#include <gnutls/gnutls.h>
#include <gnutls/crypto.h>
#include <stdio.h>
#include <string.h>

int main(void)
{
    static const unsigned char expected[32] = {
        0x8e,0xb3,0xb2,0xc2,0x7b,0xd6,0xe0,0x24,0xb4,0x0d,0x8a,0xad,0xca,0x54,0x6e,0x64,
        0x60,0x8d,0x2c,0x1e,0x1f,0x6d,0x73,0xfb,0x5e,0xd2,0x4f,0x0e,0x4b,0x16,0x70,0x28
    };
    unsigned char hash[32], random[16];
    gnutls_session_t session;
    gnutls_certificate_credentials_t credentials;
    int ret;
    if (!gnutls_check_version("3.8.13")) return 1;
    if ((ret = gnutls_global_init()) < 0) return 2;
    if (gnutls_rnd(GNUTLS_RND_RANDOM, random, sizeof(random)) < 0) return 3;
    if (gnutls_hash_fast(GNUTLS_DIG_SHA256, "Gluon", 5, hash) < 0 || memcmp(hash, expected, 32)) return 4;
    if (gnutls_certificate_allocate_credentials(&credentials) < 0) return 5;
    if (gnutls_init(&session, GNUTLS_CLIENT) < 0) return 6;
    if (gnutls_priority_set_direct(session, "NORMAL", NULL) < 0) return 7;
    if (gnutls_credentials_set(session, GNUTLS_CRD_CERTIFICATE, credentials) < 0) return 8;
    gnutls_deinit(session);
    gnutls_certificate_free_credentials(credentials);
    printf("Gluon TLS native dependencies passed: GnuTLS %s; init/random/hash/session/credentials\n", gnutls_check_version(NULL));
    gnutls_global_deinit();
    return 0;
}
C
/usr/bin/clang -arch x86_64 -O2 -Wall -Wextra -Werror -I"$DEPS/include" "$BUILD/tls-check.c" \
    "$OUT/lib/libgnutls.30.dylib" -Wl,-rpath,"$OUT/lib" -o "$BUILD/tls-check"
python3 - "$BUILD/tls-check" <<'PY'
import os
import subprocess
import sys

env = {key: value for key, value in os.environ.items()
       if not key.startswith('DYLD_') and key != 'LD_LIBRARY_PATH'}
process = subprocess.Popen([sys.argv[1]], env=env, start_new_session=True)
try:
    status = process.wait(timeout=15)
except subprocess.TimeoutExpired:
    process.kill()
    process.wait(timeout=5)
    raise SystemExit('TLS native dependency check exceeded 15 seconds')
if status:
    raise SystemExit(f'TLS native dependency check failed: {status}')
PY

L="$OUT/licenses/tls"
mkdir -p "$L"
cp "$SRC/gmp/COPYING.LESSERv3" "$L/GMP-COPYING.LESSERv3.txt"
cp "$SRC/gmp/COPYINGv2" "$L/GMP-COPYINGv2.txt"
cp "$SRC/nettle/COPYING.LESSERv3" "$L/Nettle-COPYING.LESSERv3.txt"
cp "$SRC/nettle/COPYINGv2" "$L/Nettle-COPYINGv2.txt"
cp "$SRC/gnutls/COPYING.LESSERv2" "$L/GnuTLS-COPYING.LESSERv2.txt"
cp "$SRC/gnutls/COPYING" "$L/GnuTLS-COPYING.txt"
cp "$SRC/gnutls/lib/inih/LICENSE.txt" "$L/GnuTLS-inih-LICENSE.txt"
cp "$SRC/gnutls/lib/crau/LICENSE" "$L/GnuTLS-crau-LICENSE.txt"
cp "$SRC/gnutls/lib/crau/UNLICENSE" "$L/GnuTLS-crau-UNLICENSE.txt"
sed -n '181,205p' "$SRC/gnutls/README.md" > "$L/GnuTLS-LICENSING.txt"
cat > "$L/ORIGIN.txt" <<EOF
TLS libraries compiled for x86_64 by packaging/build-tls.sh in GitHub Actions.
GNU MP 6.3.0: sources/gnutls/gmp in the published CodeWeavers open-source release
  https://media.codeweavers.com/pub/crossover/source/crossover-sources-26.3.0.tar.gz
  sha256 $CX_SRC_SHA
Nettle 4.0 (including Hogweed): $NETTLE_URL
  sha256 $NETTLE_SHA
GnuTLS 3.8.13: $GNUTLS_URL
  sha256 $GNUTLS_SHA
  Bundled mini-TASN.1 and libunistring are built into GnuTLS.
Build options: -O2, x86_64, macOS minimum 11.0, at most four parallel jobs.
Only native shared libraries are shipped; no command-line tools, tests or headers.
Optional PKCS#11, TPM, DANE, IDNA and certificate-compression dependencies are disabled.
Runtime install names and internal references use @rpath with @loader_path rpaths.
Wine must configure --with-gnutls and use @rpath/libgnutls.30.dylib as SONAME_LIBGNUTLS.
GnuTLS libraries are LGPL-2.1-or-later; GMP/Nettle and some included libunistring
  code use LGPL-3.0-or-later or GPL-2.0-or-later. License texts are adjacent.
No library from a commercial product is copied or distributed.
EOF
printf '%s\n' "$BUILD_KEY" > "$OUT/.gluon-tls-build"
echo "Source-built TLS dependencies ready: $OUT/lib/libgnutls.30.dylib"
