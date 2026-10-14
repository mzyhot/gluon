# Direct3D 9 source build and evidence

Status recorded 2026-10-13. The owner stopped local graphics activity because it
overheated the MacBook. Further builds and tests belong in GitHub Actions or
GitHub Codespaces. Reading the files described here did not launch Wine, a game,
a compiler, or a graphics probe.

## What the package builds

`packaging/build-runtime.sh` extracts `sources/dxvk` from CodeWeavers' published
`crossover-sources-26.3.0.tar.gz`. It pins the archive SHA-256 to
`ac99c8ca4b3848f3e81784135f023df266b61c2345726ea55a50b3e030dd6872`.
The source's Meson project identifies itself as DXVK `v1.10.3`.

`packaging/build-d3d9.sh` cross-compiles only `d3d9.dll` for x86_64 and i386 with
the pinned llvm-mingw toolchain. It checks the PE architecture, DLL characteristic,
`Direct3DCreate9` and `Direct3DCreate9Ex` exports, and the `vulkan-1.dll` import,
and rejects external libc++, libstdc++, libgcc, or libwinpthread DLL imports.
Both architectures must finish those checks before either installed DLL is
replaced. Wine's originals remain beside them as `d3d9.dll.wined3d` in
`lib/wine/{x86_64,i386}-windows/`. Direct3D 10/11 and DXGI continue to use DXMT.

The helper adds Wine's `Wine builtin DLL` marker in the DOS stub after checking
that the PE header begins at or beyond offset `0x60`. This makes the source-built
DLL eligible for Wine's builtin DLL directory. A loader trace saying `builtin`
therefore does not, by itself, identify the DLL as Wine's original wined3d code.

## Changes to the published source

| Patch | Reason |
|---|---|
| `dxvk-explicit-algorithm.patch` | Include the header declaring `std::transform`, rather than depending on transitive libc++ includes. |
| `dxvk-mingw-resource-manager.patch` | Remove a dummy declaration of `D3DDEVINFO_RESOURCEMANAGER` that conflicts with the pinned MinGW-w64 header. That header defines the actual resource-statistics array. |
| `dxvk-supported-metal-features.patch` | Request `geometryShader`, `fillModeNonSolid`, and `shaderCullDistance` only when advertised by the Vulkan adapter. |

The feature change is adapted from
[Sikarugir d9vk at f229921](https://github.com/Sikarugir-App/d9vk/blob/f229921c1b7f14edeadeccff04a9e65d0a016148/src/d3d9/d3d9_device.cpp#L4144).
That fork forces geometry shaders off and negotiates the other two features.
Gluon negotiates all three; it preserves geometry shaders on adapters that have
them. The patch does not implement geometry shaders or add a desktop graphics
capability to Metal.

The DXVK meta copy and blit constructors select a vertex shader that writes the
layer when `VK_EXT_shader_viewport_index_layer` is enabled. Otherwise they select
a geometry shader. The successful historical logs advertise and enable the
vertex-layer extension. They do not establish that an adapter missing both that
extension and geometry shaders works.

## Existing evidence, read after the stop request

The following files already existed in the ignored `build/` directory. They are
historical local development evidence, not results from a newly built cloud
runtime. The filenames do not substitute for a complete command record or
artifact provenance. No local run was repeated to produce this document.

| Existing file | Observation |
|---|---|
| `build/d3d9-first-build.log` | Both x86_64 and i386 builds completed; the helper reported PE machines `0x8664` and `0x014c` and installed the builtin markers. |
| `build/d3d9-metal-features-build.log` | The feature patch was applied and both architectures rebuilt; the last line reports the source-built D3D9 output ready. |
| `build/d3d9/{x86_64,i386}/pe-inspection.txt` | Each saved inspection has the expected architecture, both creation exports, and a Vulkan loader import. |
| `build/d3d9-probe/source-runtime-x64-mvk142-explicit.log` | DXVK 1.10.3 with MoltenVK 1.4.2 requested unsupported feature flags 5 and 39; `vkCreateDevice` returned `VK_ERROR_FEATURE_NOT_PRESENT` and D3D9 device creation failed with `0x8876086a`. |
| `build/d3d9-probe/source-runtime-x64-final.log` | Reports Apple M4, DXVK 1.10.3 and MoltenVK 1.4.2, with geometry and cull-distance features disabled; exact clear and triangle readback values matched and the probe printed a pass. |
| `build/d3d9-probe/source-runtime-x32-final.log` | Reports the same versions and Apple M4 through the 32-bit ABI; exact clear and triangle readback values matched and the probe printed a pass. |

The x64 final log's lines 663 and 667 contain:

```text
Clear/readback: 0xff123456 (expected 0xff123456); triangle: 0xff3366aa (expected 0xff3366aa)
D3D9 render and readback passed
```

The x86 final log contains the same values and pass at lines 687 and 691.
Consequently the existing files support a narrow historical render/readback
success for both ABIs. Cloud runtime validation and real game validation remain
unfinished for both architectures.

Some earlier logs show a different failure: a fixed-function fragment shader
compiled to Metal with two arguments assigned to `buffer(0)`, then failed shader
compilation. Their triangle sample stayed at the clear colour. Several of those
logs identify DXVK even when their filename contains `wine-original` or
`baseline`, so those filenames are not evidence of a wined3d comparison. The
change in feature negotiation explains the explicit unsupported-feature failure;
these files alone do not isolate the earlier shader-binding failure to that patch.

The final logs report a 111 by 30 swapchain. They do not demonstrate fullscreen,
device reset, textured rendering, shader-model coverage, performance, thermal
behaviour, or compatibility with a Steam game.

## Provenance and installed notices

DXVK's source license is zlib/libpng. The three patches identify Gluon's changes,
and `licenses/DXVK-BUILD.txt` records them and the source location. The package
also carries the OpenVR BSD-3-Clause notice, the SPIR-V header's MIT-style notice,
LLVM's Apache-2.0 license with LLVM exceptions, and MinGW-w64's static-runtime
notices. Vulkan headers identify Apache-2.0; the runtime carries that text with
MoltenVK. `NOTICE.md` describes these components.

No D3D9 DLL from the local development runtime is an input to this build. Build
tools are pinned upstream releases; distributed D3D9 DLLs are outputs of the
cloud source build. The separate Sikarugir project was consulted as public source,
not used as an unverified prebuilt DLL input.

## Remaining cloud validation

GitHub Actions must build from the verified published archive with the current
patches, retain both PE inspections and Meson logs, and complete package creation
before the package can be described as built successfully. Each result needs its
workflow URL and commit SHA recorded in `docs/NOTES.md`.

Codespaces can host source checks and Linux cross-builds where a suitable
toolchain is available. An ordinary Linux Codespace cannot validate the macOS
Wine driver or the Metal backend. A GitHub macOS runner without an available
graphics device likewise cannot establish Metal render/readback success merely
by compiling probes. No cloud result should be represented as a game or graphics
test unless that operation actually ran there and its log records the result.

Local graphics and runtime checks remain prohibited. Do not rerun the existing
probe executables, load native graphics libraries, launch Wine, or compile the
runtime on the owner's Mac to fill those evidence gaps.
