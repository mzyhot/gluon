# Media and controller support in the source-built runtime

Selecting the patched complete Wine runtime must retain its media bridge,
controller bus and Wine add-ons. Replacing individual OpenGL DLLs cannot achieve
this: Wine's PE and Unix halves, native dependency paths and support packages
must come from one consistent build.

`packaging/build-media.sh <verified-CW-source-archive> <deps-prefix> <runtime>`
builds the native dependencies on a macOS GitHub runner. It refuses ordinary local
execution, checks the source archive's SHA-256, compiles x86_64 with `-O2`, and
runs each component build sequentially with at most four workers. No game,
controller or graphics probe runs inside this helper.

## Sources and configuration

| Component | Pinned source | Purpose |
|---|---|---|
| GLib 2.78.0 | `sources/glib` in the verified CodeWeavers 26.3 public source archive | GStreamer's native object, threading and utility dependency |
| GStreamer 1.24.4 | Complete `sources/gstreamer` monorepo in that archive | Wine's Media Foundation/DirectShow bridge and codecs |
| SDL2 2.32.10 | [Official SDL release](https://github.com/libsdl-org/SDL/releases/tag/release-2.32.10), GitHub asset digest verified | Wine's SDL controller bus |
| libffi 3.4.6 | [Official release](https://github.com/libffi/libffi/releases/tag/v3.4.6) | GLib callbacks |
| PCRE2 10.42 | [Official release](https://github.com/PhilipHazel/pcre2/releases/tag/pcre2-10.42), hash recorded in archived GLib wrap | GLib regular expressions |
| proxy-libintl 0.4 | [Upstream tag](https://github.com/frida/proxy-libintl/tree/0.4), commit `c03e1a74b17fa7ec467e110130775409e4828a4c` | Shared NLS stub used when translations are disabled |
| FFmpeg 6.1.6 | [Official source archive](https://ffmpeg.org/releases/ffmpeg-6.1.6.tar.xz) | Software decoders used by `gst-libav` |

All archives have exact SHA-256 checks in the helper and runtime's
`licenses/media/SOURCES.txt`. GLib/GStreamer keep their LGPL license notices;
SDL2, PCRE2 and libffi carry their upstream notices. FFmpeg builds without GPL,
nonfree or version-3 features and with external dependency autodetection disabled.
Its build configuration and LGPL text accompany the runtime.
FFmpeg 6.1.6 is the newest 6.1 release in the [upstream release index](https://ffmpeg.org/releases/)
checked on 2026-10-14; it retains the library major versions used by this GStreamer
source while incorporating that branch's later fixes.

Meson cannot download wrap dependencies. The native pkg-config search is limited
to the built dependency prefix. Explicit plugin choices avoid picking optional
Homebrew codec binaries from a runner. libffi and PCRE2 link into GLib from the
same build; proxy-libintl remains a separate shared library.

The selected plugins include GStreamer's core elements, audio/video conversion,
resampling, playback/decoding, type detection, AVI/MP4/Matroska/WAV/ASF demuxing,
audio/video parsers, FFmpeg decoders and Apple-media interfaces. CGL integration
supports Apple-media buffer types; building it is separate from creating or
testing an OpenGL context.

Wine configure must require `--with-gstreamer --with-sdl`, use this prefix's
`PKG_CONFIG_LIBDIR`, and set SDL's native SONAME to
`@rpath/libSDL2-2.0.0.dylib`. GStreamer exposes all four pkg-config APIs required by
the published Wine source: core, video, audio and tag.

## Relocation

`packaging/relocate-media.py` installs only the source-built media dylibs, selected
plugins and plugin-scanner helper. It checks x86_64 headers, rewrites dependencies
to `@rpath`, adds relative paths appropriate to each runtime directory, and
rejects missing or external build dependencies. It restores ordinary ad-hoc
signatures after install-name edits; Hardened Runtime is not enabled.

The launcher points GStreamer at the selected runtime's
`lib/gstreamer-1.0` and `libexec/gstreamer-1.0/gst-plugin-scanner`. Its registry cache
belongs to the game prefix, rather than the installed runtime. Wine's bridge
already disables registry forking while initializing GStreamer, but the scanner
is included for native CI inspection and other consumers.

## Limits and verification

SDL's presence enables Wine's SDL bus; it is not proof that every controller,
rumble implementation or Steam Input configuration works. Plugin presence is
also not proof that every cutscene, codec or protected stream decodes. This build
does not include DRM plugins, every optional external codec or network TLS plugins.

Before promotion, Actions must compile the complete runtime with these headers,
verify relocated dependencies and initialize the packaged plugins. Existing game
prefixes stay intact. User game results and any cloud decode/controller probes
must be recorded separately from source or header checks. No local build,
graphics probe or Wine execution was performed while adding these helpers.

## Shared Wine add-ons

`packaging/build-addons.sh <published-Wine-source> <runtime>` packages official
Wine Mono 10.4.1 and Wine Gecko 2.47.4 shared archives into the Wine data layout.
It first verifies that the Wine source requests these versions. Mono's official
GitHub release API supplies the shared runtime and complete source archive
SHA-256s. Gecko's two official WineHQ archives are independently SHA-256 pinned.
Both x86 and x86_64 Gecko are included; Mono's shared archive contains both CLR
architectures and its normal support MSI/CAB.

The source's `mscoree/metahost.c` and `mshtml/nsembed.c` discover these shared
directories through Wine's data directory. Packaging executes no MSI, prefix or
Wine program. Wine retains control of installing its support package when needed.

Component notices are extracted from Mono's matching complete source archive.
Gecko's full embedded `about:license` HTML and OpenH264 notice are copied into
`licenses/wine-addons/gecko/` while remaining intact in both upstream archives.
`extract-addon-notices.py` handles Mozilla's optimized JAR layout as archive data;
it never loads Gecko's DLLs. Licensing origin and source archive links accompany
the runtime, and no vendor-shipped copy is used.
