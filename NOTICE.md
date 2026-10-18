# Third-party components

Gluon's own code (this repository) is released under GPLv3 (see LICENSE).

The installer package carries a Windows runtime that Gluon's build assembles from
open-source code (`packaging/build-runtime.sh`). License texts and source locations are
installed with it under `runtime/<name>/licenses/`.

| Component | License | How it gets into the package |
|---|---|---|
| Wine 11.0 with CodeWeavers' open-source changes | LGPL-2.1-or-later | compiled from CodeWeavers' published source release |
| FreeType | FreeType License | compiled from the same source release |
| MoltenVK | Apache-2.0 | KhronosGroup release |
| DXMT | LGPL-2.1-or-later | github.com/3Shain/dxmt release |
| DXVK 1.10.3 (Direct3D 9 only) | Zlib | compiled for x86_64 and i386 from CodeWeavers' published source release; build compatibility patches and Wine built-in marker added |
| DXVK headers and static runtime dependencies | Apache-2.0 with LLVM exceptions (LLVM), MinGW-w64 runtime notices, MIT (SPIR-V headers), BSD-3-Clause (OpenVR headers), Apache-2.0 (Vulkan headers) | bundled notices copied from the source archive and llvm-mingw into the runtime licenses folder |
| Steam API bridge (lsteamclient, steam.exe), optional | component-specific Valve terms, including Steamworks SDK License for lsteamclient/SDK headers and BSD-3-Clause for the helper; preserve the bridge input's original licenses | github.com/albvalort/proton-macos build of github.com/ValveSoftware/Proton's lsteamclient |
| GnuTLS 3.8.13 | LGPL-2.1-or-later for the library, with included dependency notices | compiled from the pinned official source release |
| GMP 6.3.0, Nettle/Hogweed 4.0 | LGPL-3.0-or-later / GPL-2.0-or-later alternatives | compiled from published GMP source and the pinned GNU Nettle release; notices under licenses/tls |
| GLib 2.78.0 and GStreamer 1.24.4 libraries/plugins | LGPL-2.1-or-later and included component notices | compiled from the published source archive with an explicit plugin set |
| GVDB (GLib 2.78.0's pinned submodule) | LGPL-2.1-or-later | reader/builder sources from upstream commit 0854af0fdb6d527a8d1999835ac2c5059976c210; license and source pin under licenses/media |
| SDL2 2.32.10 | Zlib | compiled from its pinned official source release |
| FFmpeg 6.1.6 | LGPL-2.1-or-later configuration | compiled without GPL, nonfree, version3 or autodetected external codecs |
| libffi 3.4.6, PCRE2 10.42, proxy-libintl 0.4 | MIT-style/libffi, BSD-style/PCRE2, LGPL-2.1/proxy-libintl | pinned public sources used by the GLib build; notices under licenses/media |
| Wine Mono 10.4.1 and Wine Gecko 2.47.4 | upstream component licenses, including Mono MIT and Gecko MPL-2.0 | pinned official shared-runtime releases; complete upstream notices preserved under licenses/wine-addons |

Source for the Wine and FreeType builds:
https://media.codeweavers.com/pub/crossover/source/crossover-sources-26.3.0.tar.gz

Gluon's Wine OpenGL dispatch changes are under LGPL-2.1-or-later, with the patch
installed as `licenses/Gluon-Wine-OpenGL.patch`. They resolve Khronos-defined
`GL_EXT_gpu_shader4` aliases when the active context supports that extension.
ANGLE and MGL were evaluated from their public source and are not shipped.

The Wine Vulkan host-boundary patch is also LGPL-2.1-or-later and ships as
`licenses/Gluon-Wine-Vulkan.patch`. Its private portability feature layout is
validated against pinned Khronos Vulkan XML; no generated Windows API is enlarged.
The display-name, app-bundle/focus, overlay-scope and optional compatibility-profile
patches are also LGPL-2.1-or-later. Every applied Wine patch and its inventory ship
under `licenses/gluon-wine-patches/`.
All TLS/media libraries are source-build outputs, with SHA-256 inputs, exact
configuration and source locations recorded beside their license texts.

Steam's native overlay is loaded, when Steam supplies it, from the user's installed
Mac Steam client. No Valve overlay library is copied into or distributed with Gluon.

No binary from any commercial product is included, and nothing from Apple's Game Porting
Toolkit.

Gluon is not affiliated with Valve, CodeWeavers or Apple. Steam and Proton are trademarks
of Valve Corporation; CrossOver is a trademark of CodeWeavers.

## Native Steam Play integration

The Steam client integration and installation design derive from NotProton,
GPL-3.0, revision 8562a6320279224125a80cd66bf8d1af9b04bb2d:
https://github.com/NotProtonNot/NotProton
Original notices are retained in the fetched source and packaged as
`steamplay/payloads/<build>/licenses/NotProton-{LICENSE,NOTICE}`. Gluon's patch
series, launcher and installation adaptations are GPLv3. The build publishes a
corresponding source archive containing the patched client sources and Dobby.

Dobby is Apache-2.0 at revision 5dfc8546954ce3b3198132ab13fddb89ee92cdd7:
https://github.com/jmpews/Dobby
Its license and the copyright/permission notice for cJSON (MIT, Dave Gamble and
contributors) ship with the payload. Third-party components retain their own
terms; Gluon's GPLv3 does not relicense them.

NotProton's separately licensed lsteamclient, Steamworks SDK headers, steam-shim,
Sparkle, fonts, CrossOver components, iconmaker and overlay shim are not built or
packaged by the Steam Play integration. Valve's proprietary Windows install
helpers are downloaded and verified on the user's machine when needed; Gluon
does not redistribute them. Steam signature resources are fetched only from the
pinned public upstream source. No proprietary Steam binary is included.
