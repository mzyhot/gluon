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
| Steam API bridge (lsteamclient, steam.exe), optional | BSD-3-Clause, Valve's Proton | github.com/albvalort/proton-macos build of github.com/ValveSoftware/Proton's lsteamclient |

Source for the Wine and FreeType builds:
https://media.codeweavers.com/pub/crossover/source/crossover-sources-26.3.0.tar.gz

No binary from any commercial product is included, and nothing from Apple's Game Porting
Toolkit.

Gluon is not affiliated with Valve, CodeWeavers or Apple. Steam and Proton are trademarks
of Valve Corporation; CrossOver is a trademark of CodeWeavers.
