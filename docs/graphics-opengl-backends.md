# OpenGL backend investigation

Source review and local reproduction: 2026-10-13. This document separates a
function-loading fix from a replacement desktop OpenGL implementation.

## Primordialis reproduction

The local trace in `build/graphics-repro/primordialis-wgl.log` shows:

- `wglCreateContext`, followed by `macdrv_context_create` with a null attribute list.
  There is no `wglCreateContextAttribsARB` request before the failure.
- macdrv reports legacy OpenGL `2.1 Metal - 91.7`, renderer Apple M4. Its independent
  core capability probe reports `4.1 Metal - 91.7`.
- The legacy context advertises `GL_EXT_gpu_shader4`.
- win32u finds the host symbol `glVertexAttribIPointer`; opengl32 subsequently rejects
  the unsuffixed name because its generated requirement is `GL_VERSION_3_0`.

Thus the immediate failure is the Windows function loader's version gate. The
underlying context remains desktop OpenGL 2.1 with extensions. A host framework
export is not sufficient evidence that a feature works in the current context.

Khronos explicitly declares `glVertexAttribIPointerEXT` an alias of
`glVertexAttribIPointer` in [the API registry](https://github.com/KhronosGroup/OpenGL-Registry/blob/main/xml/gl.xml).
The [EXT_gpu_shader4 specification](https://registry.khronos.org/OpenGL/extensions/EXT/EXT_gpu_shader4.txt)
defines integer input that stays integer, with byte, unsigned byte, short, unsigned
short, int and unsigned int array element types. An alias fallback must require the
actual extension and supported extension entry point, and preserve the signature.
Suffix guessing, returning `glVertexAttribPointer`, or removing all capability
checks would not preserve this behavior. An alias fix does not add newer GLSL or
unimplemented APIs.

## Instanced drawing follow-up (2026-10-14)

After the owner launched the complete v0.1.0 runtime, Primordialis passed the
earlier integer-attribute lookup and asserted at `glDrawArraysInstanced`
(`code\gl_functions_list.h:33`). Its current game log confirms the screenshot.
The earlier context trace advertises `GL_ARB_draw_instanced`; Wine's generated
registry requires desktop GL 3.1 for the unsuffixed function, but the ARB entry
requires the advertised extension. The same distinction applies to indexed
instanced drawing.

The pinned Khronos XML declares both ARB and EXT draw-instanced commands exact
aliases of the corresponding core commands, with identical parameter types.
The patch now adds both array and indexed commands, preferring ARB and then
trying EXT only through its own capability check. A failed first candidate must
not suppress the second. Wine returns a registry index from this resolver, so
only `(void *)-1` indicates failure; index zero remains valid. The shader4 aliases,
current-context checks before shared caches, context version and GLSL behavior
are preserved.

Static strings in all three installed Primordialis executable variants contain
the same 67 exact GL function names. Comparing those names with the Wine registry
and recorded legacy extensions identifies this draw-instanced lookup as the
additional supported alias omission; integer uniforms/attributes and the divisor
already have checked mappings. This is a source/static audit, not evidence that
all rendering or shader requirements are satisfied. The new complete runtime
still needs cloud compilation and owner-run rendering verification.

The executable variants also contain `330` immediately beside a `#version %s`
shader-preprocessor string. Referenced shader files use `gl_VertexID`, integer
attributes and explicit output locations. This is strong static evidence of a
GLSL 3.30 requirement after function loading, while the recorded game context is
legacy 2.1. The alias patch does not implement that shader language. Treat shader
compilation as a separate prerequisite to verify on the next real launch.

## Existing Wine path

CodeWeavers' published 26.3 source, `sources/wine/dlls/winemac.drv/opengl.c`, creates
CGL contexts and manages NSOpenGL views and CGL pbuffers. With null attributes,
`macdrv_context_create` retains major version 1 and `core = FALSE`; the normal CGL
legacy context is created. Explicit 3.2/3.3/4.0/4.1 requests use the core path and are
accepted only with the forward-compatible flag. 3.0, 3.1 and modern compatibility
profiles are rejected. This behavior explains the difference between the legacy
and core capabilities in the trace.

The [WGL context specification](https://registry.khronos.org/OpenGL/extensions/ARB/WGL_ARB_create_context.txt)
requires legacy context creation to preserve compatibility. A returned context at
3.2 or newer must implement the compatibility profile. Promoting every legacy
context to Apple's core profile would violate that contract. Forward compatibility
also has observable behavior: it removes deprecated functionality that remains in
the core profile, including wide lines. Ignoring this difference requires separate
evidence and implementation, not a version/profile rename.

Wine's `configure.ac` EGL detection does not automatically integrate EGL with
macdrv. `dlls/win32u/opengl.c` initializes an EGL platform only when the graphics
driver supplies `p_init_egl_platform`; macdrv supplies no such callback. The generic
EGL context creation path explicitly rejects `WGL_CONTEXT_ES2_PROFILE_BIT_EXT` and
binds `EGL_OPENGL_API`. Its X11 and Wayland surface adapters are examples of the
additional work a Mac EGL path would need.

## ANGLE Metal

Reviewed commit: `b07d4a63246a825d1f87a25501773bc8e346ff87` (2026-10-13 local time).
ANGLE is BSD licensed. No ANGLE binary is currently shipped by Gluon.

[ANGLE's support table](https://github.com/google/angle/blob/b07d4a63246a825d1f87a25501773bc8e346ff87/README.md)
lists ES 2.0 and 3.0 on Metal. This is an ES implementation with EGL; its desktop GL
backend describes a target graphics API, not a desktop GL client frontend.

The implementation confirms the boundary:

- [Metal constants](https://github.com/google/angle/blob/b07d4a63246a825d1f87a25501773bc8e346ff87/src/libANGLE/renderer/metal/mtl_common.h)
  set the maximum ES version to 3.0.
- [DisplayMtl](https://github.com/google/angle/blob/b07d4a63246a825d1f87a25501773bc8e346ff87/src/libANGLE/renderer/metal/DisplayMtl.mm)
  advertises only ES2/ES3 renderable config bits. Its conformant version is capped at
  3.0; suitable GPU families expose 3.0, otherwise 2.0. Native windows are CALayer
  objects, and Metal window/pbuffer/IOSurface adapters already exist.
- [EGL validation](https://github.com/google/angle/blob/b07d4a63246a825d1f87a25501773bc8e346ff87/src/libANGLE/validationEGL.cpp)
  rejects desktop `EGL_OPENGL_API` with `EGL_BAD_CONFIG`.

An ANGLE Metal integration is plausible for Windows applications that actually use
EGL/OpenGL ES, after adding the appropriate Wine Mac surface and ES dispatch path.
It does not supply Primordialis's desktop compatibility context. Translating desktop
GL/GLSL into ES and preserving compatibility behavior is a separate substantial
frontend project. Substituting `libGLESv2` for Wine's desktop dispatch table would
not implement that project.

## MGL

Reviewed commit: `de0ded04ec7dc99182e27e555a17775523b26911` (2026-01-12).
MGL is Apache-2.0 licensed. No MGL binary is currently shipped by Gluon.

[MGL's README](https://github.com/openglonmetal/MGL/blob/de0ded04ec7dc99182e27e555a17775523b26911/README.md)
describes partial desktop 4.6/ES3 work and functional-path tests without complete
coverage. That target should not be read as a complete supported OpenGL profile.
Its vertex/fragment/compute rendering paths and GLSL-to-SPIR-V-to-MSL structure are
useful implementation references.

Concrete gaps in the current source affect a Wine backend:

| Area | Source evidence | Consequence |
|---|---|---|
| Capability reporting | [glm_params.c](https://github.com/openglonmetal/MGL/blob/de0ded04ec7dc99182e27e555a17775523b26911/MGL/src/glm_params.c) hardcodes version 4.6 and a core profile after consulting system GL | Reported version is not a demonstrated implementation level |
| Integer attributes | [vertex_arrays.c](https://github.com/openglonmetal/MGL/blob/de0ded04ec7dc99182e27e555a17775523b26911/MGL/src/vertex_arrays.c) stores pointer and integer-pointer state identically, admits invalid floating types for integer input, rejects the integer-array query | The exact API behind the game failure needs semantic fixes |
| Extension enumeration | [get.c](https://github.com/openglonmetal/MGL/blob/de0ded04ec7dc99182e27e555a17775523b26911/MGL/src/get.c) switches `glGetStringi` on the index rather than the name; 64-bit state getters assert | Normal application capability queries can fail |
| Compatibility functions | [non_core_unimplemented.c](https://github.com/openglonmetal/MGL/blob/de0ded04ec7dc99182e27e555a17775523b26911/MGL/src/non_core_unimplemented.c) includes 370 `assert(0)` sites, including begin/end, matrices and legacy attributes | No complete compatibility profile |
| Queries | [mgl_funcs_to_be_implemented.c](https://github.com/openglonmetal/MGL/blob/de0ded04ec7dc99182e27e555a17775523b26911/MGL/src/mgl_funcs_to_be_implemented.c) has no-op query/conditional-render entry points | Exported symbols can silently omit effects |
| Context ownership | [glm_context.c](https://github.com/openglonmetal/MGL/blob/de0ded04ec7dc99182e27e555a17775523b26911/MGL/src/glm_context.c) keeps the current context in one global `_ctx` | Thread-local current contexts and sharing need implementation |
| Shader language | [shaders.c](https://github.com/openglonmetal/MGL/blob/de0ded04ec7dc99182e27e555a17775523b26911/MGL/src/shaders.c) raises older version directives to 330 and forces a core profile | Changing a directive does not translate removed language features |

The [GLFW adapter](https://github.com/openglonmetal/MGL/blob/de0ded04ec7dc99182e27e555a17775523b26911/external/glfw/src/mgl_context.m)
creates a custom GLMContext and binds an MGLRenderer to an NSView; it is not a CGL
ABI replacement. Wine integration requires dedicated context, drawable, presentation,
sharing, pixel-format and function-resolution adapters. Dropping its dylib into the
runtime would not intercept Wine's CGL path. MGL is a possible development base,
but its current implementation cannot honestly be installed as a general desktop
compatibility fix.

## Probe and remaining work

The source alias patch and its registry/signature checks are implemented. The
native Wine module compiled, but a local partial-module substitution failed
during DLL attachment before WGL calls; it is not runtime validation. Those local
experiments were stopped after the owner reported overheating. Verify the complete
CI-built Wine runtime using bounded Actions probes. Primordialis rendering is
still unresolved. Local execution follows the latest constraints in `NOTES.md`.

`packaging/check-opengl.sh [arm64|x86_64 ...]` compiles a standalone CGL probe. It
reports legacy/core versions and flags, EXT_gpu_shader4 advertisement, host core/EXT
integer-pointer state, GLSL 330 compilation, and wide-line behavior. It changes only
temporary contexts, never Steam or a Wine prefix. This is a diagnostic, not a
conformance test or replacement backend.

Validated locally: shell syntax and arm64/x86_64 compile/link with warnings treated
as errors (`--compile-only`). The first arm64 execution could not create either
context: CGL returned `invalid CoreGraphics connection` in the execution sandbox.
Context creation was not retried. Therefore no native runtime results are asserted.
Wine's existing game trace independently proves the legacy/core version distinction.

Useful immediate work is a strictly registry-backed, extension-gated function alias
fix followed by a Wine regression probe and a new Primordialis trace. Remaining
desktop compatibility and shader requirements must be established after that fix.
Shipping ANGLE or MGL as a full solution before the adapters and semantic gaps are
implemented and tested would leave the original task incomplete.

`packaging/check-opengl-aliases.py <wine-source> <gl.xml> --self-test` validates the
tracked Wine patch in memory against the authoritative registry and the extracted
Wine PE thunks and extension table. It checks every one of the 34 shader4 promotion
aliases, exact normalized C types, desktop GL 3.0 registration, extension requirements
and recursive per-context checks before the shared native pointer cache. Use
`--applied` for a source tree that already has the patch. It fetches nothing and
modifies neither input. Negative fixtures remove an XML alias, change a parameter
type, and move the capability guard behind the cache; all must be rejected.

The build wrapper `packaging/check-opengl-aliases.sh` pins Khronos registry commit
[`1cdd228e34966dd6b95bd203e9f84faba0f371a1`](https://github.com/KhronosGroup/OpenGL-Registry/commit/1cdd228e34966dd6b95bd203e9f84faba0f371a1)
and verifies XML SHA-256
`b9ca2cfa5c676e901c20d34af3407f1687cde0f1336a5ff7a8974d04c7494ad3`.
The local XML's Git blob SHA-1 also matches the repository contents metadata:
`ff263739c9100a4636f45e3c1bbb5940444c1d70`. The registry carries Apache-2.0 licensing;
it is a build validation input, with no registry binary added to the runtime.

The Windows `packaging/check-wgl.c` probe checks actual integer-array state,
unsupported compute availability, unchanged legacy version reporting, and a switch
from a 3.3 core request back to legacy. The switch first loads `glGetStringi` on core;
it must then be unavailable on legacy despite its cached host pointer. New fallback
expectations are enforced with `--require-alias`; without it the probe records the
unpatched behavior. Both x86_64 and i386 PE builds compile with warnings treated as
errors. Runtime validation results belong in `docs/NOTES.md` alongside the game trace.
