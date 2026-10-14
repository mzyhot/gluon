# OpenGL native library load failure

The existing `build/graphics-repro/wgl-patched-module-x64.log` fails during
`OPENGL32.dll` process attach. It does not establish a rendering, alias, or GPU
failure. No graphics process, native library, probe, compiler, or test was launched
for this audit.

## What the retained evidence establishes

- Log line 5758 enters the PE DLL's process attach. Line 5759 reports a debug-output
  overflow whose partial header names `get_builtin_unix_funcs`. The subsequent
  assertion and stack-limit errors occur after that overflow.
- In the published Wine source snapshot,
  `dlls/ntdll/unix/virtual.c:838–865`, the only warning in
  `get_builtin_unix_funcs` follows a failed `dlopen(unix_path, RTLD_NOW)`.
  A missing Unix-call export instead returns `STATUS_ENTRYPOINT_NOT_FOUND`
  without that warning. The earlier
  `wgl-patched-fulltrace-x64.log:120` also records `0xc0000135`
  (`STATUS_DLL_NOT_FOUND`) from `DllMain`.
- `dlls/opengl32/wgl.c:1724–1729` calls `__wine_init_unix_call` before the
  Unix `process_attach` entry. That native entry is in
  `dlls/opengl32/unix_wgl.c:2156`. The failure therefore precedes calls to the
  patched context creation/alias behavior.

The precise dyld error is absent from the retained logs. The failed temporary
runtime copies have been removed, so the old dependency resolution cannot be
reconstructed from those logs alone.

## The retained module has both Unix-call ABIs

Static symbol inspection of
`build/opengl-wine-build/dlls/opengl32/opengl32.so` shows exported
`__wine_unix_call_funcs` and `__wine_unix_call_wow64_funcs`. Its modification time
is 19:42 on October 13, 2026, before the 19:48 module log. The original,
source-verified `cx-26.3-foss` OpenGL module also exports both tables.

The generated build has `HOST_ARCH = x86_64`, native compiler flags
`clang -arch x86_64 -m64`, and PE architectures `i386 x86_64`.
`include/minwindef.h:40–42` derives `_WIN64` from `__x86_64__` for native
compilation. The WoW64 table in `dlls/opengl32/unix_thunks.c` is gated by
`_WIN64`. `tools/makedep.c:4786–4800` selects the Unix installation architecture
from `HOST_ARCH` separately from the PE architecture list.

Neither omission of `--enable-win64` nor putting `i386` first in
`--enable-archs=i386,x86_64` explains this recorded warning. A missing WoW64
export would also be the wrong explanation for this x64 load failure.

## Dependency checks and the remaining uncertainty

Both native modules link `@rpath/ntdll.so` and `@rpath/win32u.so`. The new module
has the rpath `@loader_path/`; the original also has
`@loader_path/../../../lib64`. Every new module import attributed to `ntdll.so`
or `win32u.so` exists in those source-verified runtime libraries. These checks
do not identify a missing source API export. They also do not verify the dyld
search paths, signatures, or actual files used by the removed test copy.

Wine's debug formatter permits a 2,000-byte formatted message
(`include/wine/debug.h`), while the native output buffer holds 1,020 bytes
(`dlls/ntdll/unix/debug.c:50–87`). A sufficiently long path plus `dlerror()`
message can abort the diagnostic itself. This explains why the useful error
text can be lost; the retained partial header cannot recover that text.

## Hosted verification recommendation

Use the complete runtime built from one published source tree. Before any
context/rendering test, have CI load each native module with a minimal helper
that prints `dlerror()` directly to stderr and checks both Unix-call exports.
Run x86 and x64 PE DLL-load checks against that same runtime, one at a time,
with a bounded timeout and immediate failure propagation.

If a hosted Wine load test still hits the debug overflow, cap the path and
error lengths in the `get_builtin_unix_funcs` warning, or emit the failed
`dlopen` diagnostic directly to stderr. Capture the resulting actual dyld
reason before changing architecture flags or attempting a graphics test.
