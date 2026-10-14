# Third-party components

Gluon's own code (this repository) is released under the Unlicense (see LICENSE).

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
| Steam API bridge (lsteamclient, steam.exe), optional | BSD-3-Clause, Valve's Proton | github.com/albvalort/proton-macos build of github.com/ValveSoftware/Proton's lsteamclient |

Source for the Wine and FreeType builds:
https://media.codeweavers.com/pub/crossover/source/crossover-sources-26.3.0.tar.gz

Gluon's Wine OpenGL dispatch changes are under LGPL-2.1-or-later, with the patch
installed as `licenses/Gluon-Wine-OpenGL.patch`. They resolve Khronos-defined
`GL_EXT_gpu_shader4` aliases when the active context supports that extension.
ANGLE and MGL were evaluated from their public source and are not shipped.

Steam's native overlay is loaded, when Steam supplies it, from the user's installed
Mac Steam client. No Valve overlay library is copied into or distributed with Gluon.

No binary from any commercial product is included, and nothing from Apple's Game Porting
Toolkit.

Gluon is not affiliated with Valve, CodeWeavers or Apple. Steam and Proton are trademarks
of Valve Corporation; CrossOver is a trademark of CodeWeavers.
