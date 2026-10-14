# Complete runtime verification on GitHub macOS

Both workflows passed on commit
`186bfc4d1b9f3d725697db536c38875392e3704f` on 2026-10-14:

- [Build run 38600823639](https://github.com/mzyhot/gluon/actions/runs/38600823639),
  `macos-15`, finished at 12:00:40.
- [Source checks run 38600824537](https://github.com/mzyhot/gluon/actions/runs/38600824537),
  Ubuntu 24.04. All 53 fixtures passed, including the three launcher environment
  routing fixtures skipped on macOS. Pinned source patches, all 34 OpenGL alias
  signatures and negative capability/cache cases passed.

## Runtime evidence

The macOS job compiled Wine, both source-built DXVK Direct3D 9 DLLs and both ABI
probes, then checked the complete packaged runtime. The runner exposed an Apple
Paravirtual Metal device through MoltenVK 1.4.2.

| Check | Result |
|---|---|
| Native Vulkan device creation and wait | Passed |
| Wine prefix initialization | Passed |
| OpenGL module load, x86_64 and i686 | Passed |
| Windows Vulkan loader, x86_64 and i686 | Passed |
| Direct3D 9 render/readback, x86_64 and i686 | Passed |
| Prefix server stop and wait | Passed |
| Native OpenGL context | Unavailable; exit 77 with explicit sentinel |
| WGL aliases, x86_64 and i686 | Skipped because native CGL was unavailable |

Both Direct3D 9 probes read back clear pixel `0xff123456` and triangle pixel
`0xff3366aa`, matching their expected values. Native OpenGL reported
`CGLChoosePixelFormat: invalid pixel format` and
`GLUON_PROBE_UNAVAILABLE: CGL context creation`.

These results establish the small Direct3D 9 rendering case in the complete CI
runtime. They do not establish Primordialis OpenGL rendering, presentation,
performance, real game compatibility, Steam overlay/achievement popups or an
installer run. No graphics probe, compiler, installer or extracted runtime
executable was run on the owner's Mac during artifact inspection.

## Verified artifacts

GitHub's artifact API associates both archives with the exact commit above. Each
downloaded archive's SHA-256 matched the API digest before extraction.

| Artifact | ID | Archive bytes | SHA-256 |
|---|---|---:|---|
| `Gluon-0.3.0-dev` | 11914941014 | 173601458 | `e5b1fc7419b1e48b0ca6bbaba44dfbe2f01a31206ee9808eb0d66e7de2d90315` |
| `graphics-build-evidence` | 11914681009 | 328858 | `a4660a4e48e2220643e25053b39cfd956b7da18cbafe339c16b4be0961a170d6` |

The package inside the first archive is `Gluon-0.3.0-dev.pkg`, 175008864 bytes,
SHA-256 `ceb899f8b562334479c3da37e3623ad962b72c22a5d65358c870b920575a0251`.
It matches the accompanying `.pkg.sha256` file. It is a workflow artifact; no
release or tag was created.

`pkgutil --expand-full` extracted the package without executing its installer
scripts. The runtime is at this repository-relative path in ignored build storage:

```text
build/cloud-38600823639/expanded/gluon-component.pkg/Payload/Library/Application Support/Gluon/runtime/gluon-wine
```

Its regular `bin/wine` file is 13552 bytes, SHA-256
`c747e62c3ac32a98855ff7e4ec1e67e432172f6bec025be9ab7b98dbd7b53f19`.
The Steam API bridge is present: `x86_64-unix/lsteamclient.so`, plus
`lsteamclient.dll` and `steam.exe` in both `x86_64-windows` and `i386-windows`.
`licenses/steam-bridge/` contains its LICENSE, NOTICE and ORIGIN.txt; the origin
identifies proton-macos `0.0.0-dev`, commit `cc1d07a`, workflow 38242355395 and
original archive SHA-256
`ecea4586ed3b1118fa5f41006d6f7422209185f3ed378023b2626c18551de24c`.
The runtime also contains Wine, DXVK, DXMT, MoltenVK and dependency notices,
source origins and Gluon's Wine patch.

The package's bridge files being present does not prove an achievement or overlay
test. Steam's native overlay is taken only from the user's installed Steam client;
Valve's overlay library is not part of this package or these artifacts.
