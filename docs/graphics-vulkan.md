# Vulkan through Wine and MoltenVK

Gluon's Vulkan path is the Windows `vulkan-1.dll` loader shim, Wine's
`winevulkan`/`win32u` translation, `winemac.drv` window surfaces, and MoltenVK on Metal.
MoltenVK v1.4.2 is the existing pinned, open-source Khronos release. This work adds
deterministic runtime selection and source-built packaging probes; it does not claim
to fix a reproduced Vulkan game failure or improve frame rates.

## Source findings

CodeWeavers' published Wine 26.3 source, `dlls/win32u/vulkan.c:vulkan_init_once()`,
opens `SONAME_LIBVULKAN` directly with `dlopen()`. Its `CX_LIBVULKAN` override only
applies when `CX_ACTIVE_GRAPHICS_BACKEND=wined3d`; Gluon does not need that vendor
environment contract. `configure.ac` lines 1948–1953 first searches for a system
`libvulkan`, then MoltenVK. `aclocal.m4:WINE_CHECK_SONAME` uses the autoconf cache and
extracts a bare dylib filename from the linked executable.

The build can explicitly select its pinned library by exporting these cache values
before the existing configure invocation:

```sh
export ac_cv_lib_soname_vulkan=''
export ac_cv_lib_soname_MoltenVK='@rpath/libMoltenVK.dylib'
```

The existing `@loader_path/../../` rpath on Wine's Unix modules resolves to the
runtime's `lib` directory from `lib/wine/x86_64-unix`. Selecting MoltenVK explicitly
prevents a Vulkan SDK or Homebrew loader installed on the build runner from silently
changing the shipped backend. This is a packaging determinism improvement; the
existing bare-name lookup did work in the local probe.

`dlls/winemac.drv/vulkan.c` maps `VK_KHR_win32_surface` to
`VK_EXT_metal_surface` (or the older `VK_MVK_macos_surface`). It creates the native
surface from Wine's Metal view layer. The current
[upstream Wine driver](https://github.com/wine-mirror/wine/blob/master/dlls/winemac.drv/vulkan.c)
uses the same extension mapping. Wine's generated bridge filters the extensions it
exposes to Windows callers; it is not a transparent forwarding of every MoltenVK
extension.

The
[MoltenVK v1.4.2 guide](https://github.com/KhronosGroup/MoltenVK/blob/v1.4.2/Docs/MoltenVK_Runtime_UserGuide.md)
describes a Vulkan portability implementation, rather than complete conformance.
Its SDK loader portability-enumeration requirement applies when using the Khronos
Vulkan loader. Gluon loads MoltenVK directly, so adding
`VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR` to every Windows application's
instance is not a demonstrated fix here. The same guide says MoltenVK does not load
Vulkan layers itself; Wine also rejects explicit instance layers. A validation-layer
JSON copied into this runtime would therefore not provide validation of Windows
calls.

The
[v1.4.2 configuration defaults](https://github.com/KhronosGroup/MoltenVK/blob/v1.4.2/Docs/MoltenVK_Configuration_Parameters.md)
already enable Metal argument buffers and command pooling. Semaphore style `1`
deliberately has a Rosetta fallback. These defaults remain in effect; no unmeasured
performance or synchronization overrides were added.

## Packaging checks

After installing support libraries into the CI-built runtime, run:

```sh
bash packaging/check-vulkan.sh "$OUT" "$SRC/wine" "$WORK/llvm-mingw" "$WORK/graphics-checks"
```

The helper verifies MoltenVK has an x86_64 slice, compiles a native x86_64 probe from
source, loads the exact bundled dylib, checks instance extension enumeration and
the required `VK_KHR_surface`/`VK_EXT_metal_surface` extensions, and compiles Windows
probes for x86_64 and i386 with the build's pinned llvm-mingw. It clears loader path
overrides for the native run. Probes remain in the build directory, outside the
installer payload.

The native check needs no Metal device. Windows probe execution and GPU device
creation are separate checks, with an isolated prefix, on a machine that permits
Wine and exposes a Metal device:

```sh
WINEPREFIX="$WORK/graphics-checks/prefix" \
  WINEDLLOVERRIDES='mscoree,mshtml,winemenubuilder.exe=d' \
  "$OUT/bin/wine" "$WORK/graphics-checks/vulkan-x86_64.exe"
WINEPREFIX="$WORK/graphics-checks/prefix" \
  "$OUT/bin/wine" "$WORK/graphics-checks/vulkan-i686.exe"
"$WORK/graphics-checks/vulkan-native" "$OUT/lib/libMoltenVK.dylib" --device
```

Passing `--device` to either Windows probe additionally requests Vulkan 1.1,
enumerates a GPU and graphics queue, creates a logical device, and waits for it to
be idle. The native probe enables `VK_KHR_portability_subset` when the host
advertises it. Device creation does not verify rendering, presentation, shaders,
or game compatibility.

## Local evidence (2026-10-13)

- Native x86_64 loader and extension probe passed against the open-source
  proton-macos release's MoltenVK v1.4.2: `Vulkan 1.4.357: 18 instance extensions,
  required surface extensions present`.
- Both Windows probes compiled with the pinned llvm-mingw toolchain, with
  `-Wall -Wextra -Werror`, as PE32+ x86_64 and PE32 i386.
- A native source probe using the existing bare-name lookup succeeded with loader
  override environment variables unset. A second probe using
  `@rpath/libMoltenVK.dylib` and the runtime's relative module rpath also succeeded.
  No missing-library defect was reproduced.
- Native instance creation returned `VK_ERROR_INCOMPATIBLE_DRIVER` (`-9`), with
  MoltenVK reporting that Metal is not available to the tool process. GPU device
  checks were unavailable; no repeated GPU attempt was made.
- The one Windows x86_64 probe launch used a new
  `build/vulkan-source-review/prefix`. It stopped at
  `wineserver: bind: Operation not permitted`, before the probe ran. It was not
  retried. Windows extension enumeration and both Windows device checks remain
  untested. No existing game prefix, Steam process, or user data was changed.
- The helper itself passed end to end against a test runtime layout pointing to
  that upstream MoltenVK release. The CI-built runtime, installer, Vulkan
  presentation, and games still require validation on a suitable Mac.
