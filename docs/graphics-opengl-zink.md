# OpenGL above 4.1 through Zink

Source review: 2026-10-16. This document asks whether Mesa's Zink (OpenGL on
Vulkan), running on MoltenVK or on Mesa's KosmicKrisp Vulkan-on-Metal driver,
can give Windows games a newer OpenGL than macdrv's CGL 4.1 core profile.
Nothing was compiled or executed for it; every finding below is a source or
documentation reading, and projected versions are marked as projections.

## Recommendation: no-go for now

- **MoltenVK (the current runtime):** upstream Zink refuses to create a screen
  on MoltenVK 1.4.2. If that check were relaxed, Mesa would compute OpenGL 2.1
  with no core profile, which is below what macdrv already provides.
- **KosmicKrisp:** it passes Zink's start-up checks, but Mesa's version ladder
  stops at OpenGL 2.1 because KosmicKrisp has no transform feedback. Reaching
  4.x depends on two KosmicKrisp merge requests that are still open (geometry
  shaders, and a draft for transform feedback). KosmicKrisp also needs macOS 26
  and Apple silicon, and has not been shown to work from Wine's x86_64
  (Rosetta) process.
- **Reports of "OpenGL 4.6 on Zink + KosmicKrisp"** set
  `MESA_GL_VERSION_OVERRIDE=4.6`. A forced version string without transform
  feedback or geometry shaders is the same problem
  [graphics-opengl-backends.md](graphics-opengl-backends.md) found in MGL's
  hardcoded 4.6.

Nothing builds Zink yet; a build-only CI job is step 1 of the prototype plan
in section 4. If the triggers below are met, the integration to prototype is
Mesa's **Windows WGL build of Zink loaded as the game's `opengl32.dll`**. It
goes through Wine's existing Vulkan path rather than a new macdrv EGL backend
(section 2).

Revisit when:

1. [mesa!44786](https://gitlab.freedesktop.org/mesa/mesa/-/merge_requests/44786)
   (KosmicKrisp geometry shaders) and
   [mesa!44928](https://gitlab.freedesktop.org/mesa/mesa/-/merge_requests/44928)
   (KosmicKrisp `VK_EXT_transform_feedback`, draft, depends on !44786) are in a
   Mesa release; or
2. MoltenVK gains robustness2 `nullDescriptor`, transform feedback and geometry
   shaders. Its main branch on 2026-10-13 still has none of the three.

## Pinned sources

| Source | Revision | License |
|---|---|---|
| Mesa 26.2.5 (latest stable) | tag `mesa-26.2.5`, commit [`ddaf85e353442d9d7f192fdcf1823e652e3f2ee3`](https://gitlab.freedesktop.org/mesa/mesa/-/tree/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3) | MIT for Zink, KosmicKrisp, Gallium, NIR and the WGL frontend; see [docs/license.rst](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/docs/license.rst) |
| Mesa main | [`86c7176d2dd0065555521bf8c61f208bf53c75d6`](https://gitlab.freedesktop.org/mesa/mesa/-/tree/86c7176d2dd0065555521bf8c61f208bf53c75d6) (2026-10-16) | same |
| MoltenVK v1.4.2 (shipped by Gluon) | [`db66022459ffb663aa2b50f6b018bc2e124f5edf`](https://github.com/KhronosGroup/MoltenVK/tree/db66022459ffb663aa2b50f6b018bc2e124f5edf) | Apache-2.0 |
| MoltenVK main | [`5189f8de86ec365860f63bf1da736815cfaea5cd`](https://github.com/KhronosGroup/MoltenVK/tree/5189f8de86ec365860f63bf1da736815cfaea5cd) (2026-10-13) | Apache-2.0 |
| Wine in CodeWeavers' CrossOver 26.3 source | archive SHA-256 `ac99c8ca4b3848f3e81784135f023df266b61c2345726ea55a50b3e030dd6872`, as pinned in `packaging/build-runtime.sh` | LGPL-2.1-or-later |
| Wine master | [`7298a818b79446bae153b8d5aa27235ab3703bf9`](https://gitlab.winehq.org/wine/wine/-/tree/7298a818b79446bae153b8d5aa27235ab3703bf9) | LGPL-2.1-or-later |

Wine paths without a link refer to the CrossOver 26.3 source.

## 1. What Zink reaches on Apple GPUs

### How Mesa picks the version

Zink reports a GLSL feature level of 460 regardless of the device
([zink_screen.c:981](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/src/gallium/drivers/zink/zink_screen.c#L981)),
so the OpenGL version comes from Mesa's extension ladder in
[main/version.c](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/src/mesa/main/version.c#L269).
OpenGL 3.0 requires `EXT_transform_feedback` (line 289). Zink enables stream
output only when the Vulkan device has `VK_EXT_transform_feedback`
([zink_screen.c:956](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/src/gallium/drivers/zink/zink_screen.c#L956)).
OpenGL 4.0 also needs tessellation and transform feedback 2/3 (line 321 on),
and 4.3 needs compute and storage buffers (line 356 on). Core profiles start at
3.1, so a 2.1 result means no core profile at all.

Mesa enables some extensions independently of the version; `ARB_compute_shader`
is one. A game that calls `wglCreateContextAttribsARB` for a 4.3 core context
still fails when the computed version is lower.

Two checks fail screen creation outright: robustness2 `nullDescriptor`
([zink_screen.c:3521](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/src/gallium/drivers/zink/zink_screen.c#L3521))
and timeline semaphores
([line 3579](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/src/gallium/drivers/zink/zink_screen.c#L3579)).
The other per-version requirements are listed in Zink's
[driver documentation](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/docs/drivers/zink.rst).
That document also says Zink on macOS "is experimental with very limited
capabilities" (line 353).

### Requirement table

Sources:
- MoltenVK v1.4.2:
  [`MVKPhysicalDevice::initFeatures`](https://github.com/KhronosGroup/MoltenVK/blob/db66022459ffb663aa2b50f6b018bc2e124f5edf/MoltenVK/MoltenVK/GPUObjects/MVKDevice.mm#L2758)
  and the
  [supported-extension list](https://github.com/KhronosGroup/MoltenVK/blob/db66022459ffb663aa2b50f6b018bc2e124f5edf/Docs/MoltenVK_Runtime_UserGuide.md#L232).
- KosmicKrisp 26.2.5:
  [kk_physical_device.c](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/src/kosmickrisp/vulkan/kk_physical_device.c#L40).
- KosmicKrisp main:
  [kk_physical_device.c](https://gitlab.freedesktop.org/mesa/mesa/-/blob/86c7176d2dd0065555521bf8c61f208bf53c75d6/src/kosmickrisp/vulkan/kk_physical_device.c#L170).

| Zink needs (GL level) | MoltenVK 1.4.2 | KosmicKrisp 26.2.5 | KosmicKrisp main |
|---|---|---|---|
| robustness2 `nullDescriptor` (screen creation) | **no**, hard-coded false ([line 638](https://github.com/KhronosGroup/MoltenVK/blob/db66022459ffb663aa2b50f6b018bc2e124f5edf/MoltenVK/MoltenVK/GPUObjects/MVKDevice.mm#L638)) | yes | yes |
| timeline semaphores (screen creation) | yes | yes | yes |
| `logicOp` (2.1) | only with MoltenVK's private Metal API option ([line 2765](https://github.com/KhronosGroup/MoltenVK/blob/db66022459ffb663aa2b50f6b018bc2e124f5edf/MoltenVK/MoltenVK/GPUObjects/MVKDevice.mm#L2765)) | yes | yes |
| `fillModeNonSolid` (2.1) | yes | no | no |
| `VK_EXT_custom_border_color` (2.1) | no | no | behind an experimental flag |
| `VK_EXT_provoking_vertex` (2.1) | yes | no | yes |
| stippled and smooth lines (2.1) | Bresenham lines only | Bresenham lines only | Bresenham lines only |
| `VK_EXT_transform_feedback` (3.0) | **no** | **no** | **no** (draft !44928) |
| `VK_EXT_conditional_rendering` (3.0) | no | yes | yes |
| `geometryShader` (3.2) | **no** | **no** | **no** (!44786) |
| `VK_EXT_depth_clip_enable` (3.2) | no (main: yes) | no | yes |
| `dualSrcBlend`, vertex attribute divisor (3.3) | yes | yes | yes |
| `tessellationShader`, `sampleRateShading`, `imageCubeArray` (4.0) | yes | yes | yes |
| `multiViewport` (4.1) | yes | yes | yes |
| `shaderCullDistance` (4.5) | no | yes | yes |
| anisotropy, `depthBiasClamp`, draw indirect count (4.6) | yes | yes | yes |
| `shaderFloat64` | no | no | no |

Zink lowers 64-bit floats in software when `shaderFloat64` is missing
([zink_compiler.c:1315](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/src/gallium/drivers/zink/zink_compiler.c#L1315)),
so fp64 is a performance issue, not a version gate. Line stipple, smooth lines
and wide lines are emulated by Zink with geometry shaders, so on these devices
those features wait on geometry-shader support as well.

### Result per Vulkan driver

**MoltenVK 1.4.2 (Gluon's current runtime):** Zink 26.2.5 stops at the
`nullDescriptor` check. Two upstream changes would make that requirement optional:
[mesa!37115](https://gitlab.freedesktop.org/mesa/mesa/-/merge_requests/37115)
is open, and [mesa!44047](https://gitlab.freedesktop.org/mesa/mesa/-/merge_requests/44047)
was closed without merging. With either applied, the ladder would still stop
at 2.1, because MoltenVK has no transform feedback, geometry shaders,
conditional rendering or custom border colors. MoltenVK main does not change
this ([MVKDevice.mm:638](https://github.com/KhronosGroup/MoltenVK/blob/5189f8de86ec365860f63bf1da736815cfaea5cd/MoltenVK/MoltenVK/GPUObjects/MVKDevice.mm#L638)).
This is no improvement on macdrv's CGL 4.1 core profile.

**KosmicKrisp 26.2.5 and main:** the hard checks pass, but the computed version
is 2.1. With !44786 and !44928 merged, every Zink requirement listed above for
3.0–4.6 would be met except `fillModeNonSolid` and the line modes. That makes
4.x plausible, possibly 4.6. Format- and limit-dependent items (RGB32 uniform
texel buffers for 4.0, `B10G11R11` vertex buffers for 4.4, the 4.1 image
limits) cannot be read from source, so the real version must be measured on a
Mac. The !44928 description builds transform feedback on the geometry-shader
emulation that Honeykrisp (Mesa's Vulkan driver for Apple GPUs under Asahi
Linux) already uses. Neither merge request reports performance.

**Community reports of OpenGL 4.6:**
- A [Minecraft write-up](https://gist.github.com/lucamignatti/5312f5e937de2ba44256ecba6de54cc2)
  (Mesa 26.0-devel fork) sets `MESA_GL_VERSION_OVERRIDE=4.6` and
  `MESA_GLSL_VERSION_OVERRIDE=460`. It also carries out-of-tree Metal
  presentation patches and the !37115 workaround. Its comments mention a mod
  failing on `GL_MAX_TRANSFORM_FEEDBACK_BUFFERS` and about a quarter of
  native frame rates.
- A [mesa-dev post from May 2026](http://www.mail-archive.com/mesa-dev@lists.freedesktop.org/msg225151.html)
  says 4.6 contexts work end to end, without saying how the version is reached.

An override makes a 4.6 context creatable, but does not implement the missing
stages. A game that uses them fails at draw time instead of at context creation.

**KosmicKrisp platform limits:**
- It is built on Metal 4 and needs macOS 26 or later
  ([kosmickrisp.rst:4](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/docs/drivers/kosmickrisp.rst#L4))
  and Apple silicon.
- Wine's Unix side is x86_64 under Rosetta, so KosmicKrisp would have to be an
  x86_64 dylib in that process. `src/kosmickrisp` contains no
  architecture-specific code, but nothing upstream shows Metal 4 working from a
  Rosetta process.
- win32u opens one Vulkan library chosen at configure time
  ([graphics-vulkan.md](graphics-vulkan.md)). Without a Wine change that selects
  the library per game, switching to KosmicKrisp would also move DXVK's D3D9 and
  native Vulkan games off MoltenVK.

## 2. Loading Zink into a Wine process

There are two places to put Zink. They differ mainly in presentation.

### A. Native, through win32u's EGL path and a macdrv backend

Wine 11's `dlls/win32u/opengl.c` already has a generic EGL implementation of
WGL:

- `egl_init` (line 869) loads `SONAME_LIBEGL`.
- The `egldrv` functions use `EGL_PLATFORM_SURFACELESS_MESA` (line 440). They
  build WGL pixel formats from every `EGL_OPENGL_BIT` config, crossed with
  GDI-capable, double-buffered and offscreen variants (lines 452 and 508).
- They map `WGL_CONTEXT_*` attributes, including the profile mask, to EGL and
  create contexts with `EGL_KHR_no_config_context` and the share context
  (line 745). ES profiles are rejected.
- Window drawables are framebuffer objects inside the context (lines 177–431).

None of this is compiled into Gluon's runtime: configure found no libEGL, so
`SONAME_LIBEGL` is undefined in the CrossOver 26.3 build's `include/config.h`.
`display_funcs_init` (line 2482) lets macdrv replace the EGL functions with its
CGL ones, and macdrv supplies no `p_init_egl_platform`.

Presentation is what is missing:

- In Wine master, a framebuffer drawable reaches the screen only when the
  display driver supplies a target drawable to blit into
  ([framebuffer_surface_swap](https://gitlab.winehq.org/wine/wine/-/blob/7298a818b79446bae153b8d5aa27235ab3703bf9/dlls/win32u/opengl.c#L1104)).
  The generic driver passes none
  ([egldrv_surface_create](https://gitlab.winehq.org/wine/wine/-/blob/7298a818b79446bae153b8d5aa27235ab3703bf9/dlls/win32u/opengl.c#L1431)).
- On Mesa's side, EGL can be built on Darwin with Zink since
  [mesa!41359](https://gitlab.freedesktop.org/mesa/mesa/-/merge_requests/41359)
  ([meson.build:364](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/meson.build#L364)),
  but only with the surfaceless and device platforms, which have no window
  surfaces.
- Zink's window-system layer (kopper) handles XCB, Wayland and Win32 surfaces
  only
  ([zink_kopper.c:68](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/src/gallium/drivers/zink/zink_kopper.c#L68)).
  Metal surface support,
  [mesa!33060](https://gitlab.freedesktop.org/mesa/mesa/-/merge_requests/33060),
  has been open since January 2025. Its author describes its way of obtaining a
  `CALayer` as an intentional hack.
- Zink's macOS build still requires MoltenVK headers even when targeting
  KosmicKrisp
  ([zink/meson.build:80](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/src/gallium/drivers/zink/meson.build#L80);
  [mesa!38605](https://gitlab.freedesktop.org/mesa/mesa/-/merge_requests/38605)
  is open). It loads `@rpath/libvulkan.1.dylib` rather than a driver dylib.

This route therefore needs four pieces:
- a Mesa Metal kopper surface, plus an EGL window surface or an
  IOSurface-sharing presenter;
- a macdrv EGL backend (`p_init_egl_platform`, window surfaces that target the
  Cocoa view's Metal layer, and swap);
- a Wine build linked against an x86_64 Mesa libEGL;
- a per-process switch, because CGL and EGL contexts cannot share objects.

Pixel formats and context sharing would come from win32u's existing EGL code.
Everything else is new, and most of it does not exist upstream.

### B. Windows side: Mesa's WGL build as the game's `opengl32.dll`

Mesa builds Windows `opengl32.dll`, a thin loader, and `libgallium_wgl.dll`,
which contains the drivers
([targets/wgl](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/src/gallium/targets/wgl/meson.build),
[targets/libgl-gdi](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/src/gallium/targets/libgl-gdi/meson.build)).
`GALLIUM_DRIVER=zink`, or a build containing only Zink, selects Zink
([wgl.c:98](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/src/gallium/targets/wgl/wgl.c#L98)).

- **Vulkan:** on Windows, Zink loads `vulkan-1.dll`
  ([zink_screen.c:70](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/src/gallium/drivers/zink/zink_screen.c#L70)),
  which inside Wine is Wine's loader and winevulkan, then the host MoltenVK or
  KosmicKrisp. Wine's `UNSUPPORTED_EXTENSIONS` list (`dlls/winevulkan/make_vulkan`,
  lines 89–121) blocks none of the extensions Zink uses. The device-feature
  gaps above apply unchanged, because Wine forwards host features.
- **Presentation:** kopper's Win32 path creates a `VK_KHR_win32_surface` for the
  window (`zink_kopper.c` lines 79 and 115–118). winemac.drv maps that surface
  to `VK_EXT_metal_surface` on the window's Metal view. This is the path
  Gluon's DXVK Direct3D 9 already presents through, so no Wine or Mesa
  presentation code is needed.
- **WGL pixel formats and contexts:** Mesa's own WGL frontend implements pixel
  formats, including Zink's depth/stencil handling through kopper
  (`stw_pixelformat.c:197`). It also provides `WGL_ARB_create_context`,
  `_profile` and `_robustness`, `WGL_ARB_pixel_format`, pbuffers,
  `render_texture`, `make_current_read`, `WGL_EXT_swap_control` and
  `wglShareLists`. A compatibility-profile request gets Mesa's real
  compatibility profile at the computed version; no profile is renamed.
- **GDI entry points:** Wine's gdi32 forwards `ChoosePixelFormat`,
  `DescribePixelFormat`, `GetPixelFormat`, `SetPixelFormat` and `SwapBuffers`
  to whichever `opengl32.dll` is loaded (`dlls/gdi32/opengl.c:43–110`), so
  games that use the GDI calls also reach Mesa.
- **32-bit games:** a separate i686 build, the same as DXVK's D3D9 in
  `packaging/build-d3d9.sh`.
- **Per-game selection:** Wine searches its own built-in directory before
  `WINEDLLPATH` (`dlls/ntdll/unix/loader.c:387–408`), so a second built-in
  directory cannot shadow Wine's `opengl32`. Gluon already uses one prefix per
  game, so the narrowest switch is to install Mesa's two DLLs into that game's
  `system32`/`syswow64` with `WINEDLLOVERRIDES=opengl32=n,b`. Wine's
  `opengl32` and macdrv's CGL path stay unchanged for every other game.
- **Costs:**
  - Mesa disables its on-disk shader cache on Windows hosts
    ([meson.build:1174](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/meson.build#L1174)),
    so every launch recompiles shaders through NIR, SPIR-V and Metal.
  - Every Vulkan call crosses Wine's thunks.
  - The native Steam overlay would see a MoltenVK or KosmicKrisp presentation
    rather than CGL, which is unverified
    ([graphics-steam-overlay.md](graphics-steam-overlay.md)).

Option B needs no Wine patch to present a frame, takes WGL semantics from Mesa,
and builds with the toolchain Gluon already pins. It is the route to prototype.
It does not remove any blocker from section 1: on MoltenVK, Zink still fails
to start.

## 3. Build requirements, size and license

### Option B on GitHub Actions

| Need | Source | Status in this repo |
|---|---|---|
| x86_64 and i686 MinGW compilers | llvm-mingw | already pinned (`20261006`, SHA-256 in `packaging/build-runtime.sh`) |
| Meson ≥ 1.4.0 ([meson.build:9](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/meson.build#L9)), Ninja | release tarballs | Meson 1.12.1 and Ninja 1.13.2 already pinned in `build-d3d9.sh` |
| Python 3 with Mako ≥ 0.8.0, PyYAML and `packaging` ([meson.build:1101–1146](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/meson.build#L1101)) | PyPI | new; install into a venv with `--require-hashes` |
| bison and flex (the GLSL compiler; required for any OpenGL build) | Homebrew | already installed by the Build workflow |
| glslang | not needed: only the overlay layer, ACO tests and BVH builds use it ([meson.build:740](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/meson.build#L740)) | — |
| LLVM | not needed: Zink does not use it, and llvmpipe is left out | — |
| zlib | only used for shader-cache compression, which Windows builds disable | disable, or build from a pinned source |

Use the Mesa release tarball for the pinned tag, with its SHA-256, and a
cross file like `build-d3d9.sh`'s. Mesa's upstream CI builds the Windows Zink
target only with MSVC
([mesa_build.ps1:52](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/.gitlab-ci/windows/mesa_build.ps1#L52)).
MinGW builds are maintained downstream: for example,
[mesa-dist-win](https://github.com/pal1000/mesa-dist-win) ships MSYS2 MinGW
releases that include Zink for x64 and x86. A clang-based llvm-mingw cross
build from macOS is therefore not upstream-tested and may need small build
fixes. Compilation runs on the runner; no Metal SDK or GPU is involved.

**Size:** the mesa-dist-win 26.2.4 MinGW release
(`mesa3d-26.2.4-release-mingw.7z`, SHA-256
`36a30a04d8cab6e2bea2d3c747c326d15024d9711dd80f45ef97ac6f2c3c5086`; contents
listed, nothing extracted or run) contains:

| File | Build | Size |
|---|---|---|
| `libgallium_wgl.dll` | x86, no LLVM; Zink, D3D12 and softpipe | 27.9 MB |
| `libgallium_wgl.dll` | x64, with LLVM's llvmpipe | 76.5 MB |
| `opengl32.dll` | x64 / x86 | 0.12 / 0.15 MB |

A Zink-only build without LLVM should be at or below the x86 figure for each
architecture, so at most about 56 MB uncompressed for both. The first build
job must measure it.

### Options A and KosmicKrisp

- **Native Zink:** an x86_64 Darwin cross file like `build-media.sh`'s, MoltenVK
  headers (required by Zink's macOS build), the same Python tools, and the
  out-of-tree presentation patches described in section 2A.
- **KosmicKrisp:**
  - It uses Metal 4, so it needs the macOS 26 SDK. GitHub's `macos-26` runner
    is [generally available](https://github.blog/changelog/2026-02-26-macos-26-is-now-generally-available-for-github-hosted-runners/)
    and ships Xcode 26.
  - Its internal OpenCL C kernels need LLVM ≥ 20.1.8, libclc,
    SPIRV-LLVM-Translator and SPIRV-Tools at build time
    ([kosmickrisp.rst:23](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/docs/drivers/kosmickrisp.rst#L23)).
  - An x86_64 cross build needs a native `kk_clc` host tool
    ([clc/meson.build:5](https://gitlab.freedesktop.org/mesa/mesa/-/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/src/kosmickrisp/clc/meson.build#L5)).
  - Hosted runners expose an Apple paravirtual GPU
    ([graphics-vulkan.md](graphics-vulkan.md)), so a run there cannot validate
    KosmicKrisp rendering. Owner-run checks would be needed.

### License

The directories a Zink-only WGL build compiles carry these SPDX identifiers:
- 301 MIT;
- 41 Apache-2.0 (Khronos headers);
- 2 BSL-1.0 (`src/c11/impl/threads_*.c`).

The only GPL-tagged files nearby are Linux UAPI headers under
`include/drm-uapi` (GPL-2.0 with the Linux syscall note). Zink includes one of
them only when libdrm is present (`zink_screen.c:92–95`), which never applies to
Windows or macOS builds. KosmicKrisp is MIT. Mesa keeps the full texts under
`licenses/`.

This fits `NOTICE.md`'s policy for separately licensed runtime components, like
DXVK and MoltenVK. No CodeWeavers code is involved, and the Gluon Public
License does not apply to these files. A shipped build would add Mesa's MIT and
BSL-1.0 notices and the llvm-mingw runtime notices that the D3D9 build already
copies.

## 4. Smallest prototype, once a trigger is met

Each step is bounded and cloud-first. A step that needs a GPU is a single
owner-run check, in line with [CLOUD-DEVELOPMENT.md](CLOUD-DEVELOPMENT.md).

1. **Build only.** Add a `workflow_dispatch` job that downloads the pinned Mesa
   release tarball and checks its SHA-256. It builds `opengl32.dll` and
   `libgallium_wgl.dll` for x86_64 and i686 with llvm-mingw, with
   `-Dgallium-drivers=zink`, no LLVM, no EGL/GLES and no shader cache. It
   verifies:
   - the PE exports `wglCreateContext`, `wglSetPixelFormat` and
     `DrvSetPixelFormat`;
   - no external compiler-runtime DLL imports;
   - the file sizes.

   It uploads them as a 7-day artifact and installs nothing into the runtime.
2. **KosmicKrisp under Rosetta.** On `macos-26`, build only KosmicKrisp as an
   x86_64 dylib. In one owner-run x86_64 native probe, modeled on
   `packaging/probes/vulkan.c`, create a device and print `nullDescriptor`,
   `transformFeedback` and `geometryShader`. If Metal 4 is unavailable to a
   Rosetta process, stop here.
3. **Per-game Vulkan library.** Write a small Wine patch so win32u can open a
   different host Vulkan library for one game, keeping MoltenVK as the default.
4. **One WGL probe.** Extend `packaging/check-wgl.c` to request a 4.3 core
   context and run a compute shader with a storage-buffer readback. Run it in
   one disposable prefix with the Mesa DLLs and the override, and no
   `MESA_GL_VERSION_OVERRIDE`. Record `GL_VERSION` and `GL_RENDERER`.
5. **One game.** Choose a game that needs 4.2 or later, enable the backend
   behind a per-game setting, and compare against macdrv.

Until then, games that need OpenGL 4.2 or newer stay unsupported. macdrv's 4.1
core profile and the compatibility-as-core mapping remain the OpenGL path.
