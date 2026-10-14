# Runtime dependencies when activating the graphics fixes

The `186bfc4` cloud runtime contains the OpenGL dispatch patch and Vulkan/D3D9
changes, but intentionally omitted several dependencies that existed in the local
development runtime. Promoting it globally without restoring those dependencies
would reduce compatibility. Preserve the previous runtime until the complete new
build passes its dependency checks. No commercial-product binary is an input to
this restoration.

## TLS through Wine

Wine's `secur32` SChannel provider and `bcrypt` native implementation use GnuTLS.
`--without-gnutls` does not provide an alternative macOS TLS implementation:
`dlls/secur32/schannel_gnutls.c` is conditional on `SONAME_LIBGNUTLS`, and
`SECUR32_initSchannelSP` stops registering SChannel if its native provider fails.
The old development `secur32.so` references GnuTLS; the `186bfc4` module does not.
Game-bundled TLS implementations, such as Chromium's, are a separate path and
cannot establish Wine WinHTTP/WinINet TLS support.

`packaging/build-tls.sh` compiles the provider dependencies for x86_64 with `-O2`
and at most four parallel jobs, guarded by `packaging/require-cloud.sh`. It runs
only on GitHub macOS. The inputs are:

| Component | Source | SHA-256 |
|---|---|---|
| GNU MP 6.3.0 | `sources/gnutls/gmp` in the already pinned [published CodeWeavers source archive](https://media.codeweavers.com/pub/crossover/source/crossover-sources-26.3.0.tar.gz) | `ac99c8ca4b3848f3e81784135f023df266b61c2345726ea55a50b3e030dd6872` |
| Nettle 4.0 and Hogweed | [Official GNU source release](https://ftp.gnu.org/gnu/nettle/nettle-4.0.tar.gz) | `3addbc00da01846b232fb3bc453538ea5468da43033f21bb345cb1e9073f5094` |
| GnuTLS 3.8.13 | [Official GnuTLS source release](https://www.gnupg.org/ftp/gcrypt/gnutls/v3.8/gnutls-3.8.13.tar.xz) | `ffed8ec1bf09c2426d4f14aae377de4753b53e537d685e604e99a8b16ca9c97e` |

The CodeWeavers archive includes GnuTLS 3.8.3 and Nettle 3.10, but its Nettle
directory lacks both `configure` and the required top-level `Makefile.in`.
Use the complete maintained releases above instead of fabricating missing files
or using the older TLS implementation. Nettle's maintainer
[announced 4.0 on 2026-02-05](https://lists.lysator.liu.se/mailman/hyperkitty/list/nettle-bugs%40lists.lysator.liu.se/thread/D4IKICV4QZHHGRXW3D6VS7GTMLBZF65C/?sort=date).
The [GnuTLS release directory](https://www.gnupg.org/ftp/gcrypt/gnutls/v3.8/)
lists 3.8.13, released 2026-04-29; its included `NEWS` confirms Nettle 4.0
support. The source archives and their version/configuration files were inspected
without running a compiler or native library on the owner's Mac.

GnuTLS uses its included mini-TASN.1 and libunistring implementations. PKCS#11,
TPM, DANE, IDNA and compression dependencies are omitted; they are not needed by
Wine's SChannel provider. Core TLS and certificate verification remain enabled.
No TLS command-line tools, documentation, development headers or test programs are
shipped. License texts and exact source origins go under `licenses/tls/`, including
the LGPL-3.0-or-later/GPL-2.0-or-later alternatives of GMP/Nettle and included
libunistring code.

The helper leaves development libraries under the build dependency prefix so
subsequent Wine configure checks can link them. It copies only the runtime dylibs
to `runtime/lib/`: `libgnutls.30.dylib`, `libnettle.9.dylib`,
`libhogweed.7.dylib` and `libgmp.10.dylib`. Their IDs/internal dependencies use
`@rpath`, with `@loader_path` rpaths; dependencies outside that closed set and
Apple's system libraries are rejected. Every shipped dylib must contain x86_64.

### Runtime builder integration

Before Wine configuration, call:

```sh
WORK="$WORK" JOBS="$JOBS" "$ROOT/packaging/build-tls.sh" \
    "$DL/crossover-sources.tar.gz" "$DEPS" "$OUT"
```

Wine must replace `--without-gnutls` with `--with-gnutls` and receive:

```sh
export ac_cv_lib_soname_gnutls='@rpath/libgnutls.30.dylib'
GNUTLS_CFLAGS="-I$DEPS/include"
GNUTLS_LIBS="-L$DEPS/lib -lgnutls"
```

The helper supplies `$DEPS/lib/pkgconfig/gnutls.pc`, plus GMP/Nettle/Hogweed
metadata. It isolates pkg-config to the x86_64 dependency prefix, so an arm64
Homebrew library cannot silently satisfy the checks. The GitHub runner needs
`pkg-config`; the released sources contain generated configure files and require
no extra autotools generation.

### Verification boundary

The helper performs a bounded native check in GitHub Actions using only the
relocated runtime copies. It checks library initialization, secure randomness,
a known SHA-256 digest, TLS session/priority setup and certificate credentials,
with a 15-second deadline. This proves native dependency loading and those calls;
it does not prove a complete handshake through Wine. A complete-runtime SChannel
or WinHTTP test must cover that boundary before promotion. Local checks are shell
syntax and source inspection only; successful cloud build/test evidence remains
required.
