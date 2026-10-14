#!/usr/bin/env python3
"""Copy source-built media libraries and reject unbundled dylib dependencies.

Only called on macOS Actions by build-media.sh. This is file/header inspection and
install-name editing; it does not load libraries, controllers or a graphics API.
"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess


def run(*command):
    return subprocess.check_output(command, text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", type=Path)
    parser.add_argument("runtime", type=Path)
    args = parser.parse_args()
    prefix = args.prefix.resolve()
    runtime = args.runtime.resolve()
    patterns = ("libglib-*.dylib", "libgobject-*.dylib", "libgmodule-*.dylib",
                "libgio-*.dylib", "libgthread-*.dylib", "libgst*.dylib",
                "libintl*.dylib", "libav*.dylib", "libsw*.dylib", "libSDL2*.dylib")
    sources = set()
    for pattern in patterns:
        sources.update((prefix / "lib").glob(pattern))
    sources.update((prefix / "lib/gstreamer-1.0").glob("*.dylib"))
    sources.add(prefix / "libexec/gstreamer-1.0/gst-plugin-scanner")
    files = []
    for source in sorted(sources):
        if not source.exists():
            raise ValueError(f"Missing built media component: {source}")
        if not source.resolve().is_relative_to(prefix):
            raise ValueError(f"Media symlink leaves the source build: {source}")
        destination = runtime / source.relative_to(prefix)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            target = source.readlink()
            if target.is_absolute():
                target = Path(target.name)
            destination.unlink(missing_ok=True)
            destination.symlink_to(target)
        else:
            shutil.copy2(source, destination)
            files.append(destination)

    if not (runtime / "lib/libSDL2-2.0.0.dylib").exists():
        raise ValueError("SDL's expected runtime SONAME is absent")
    required = ("libgstcoreelements.dylib", "libgstaudioconvert.dylib",
                "libgstvideoconvertscale.dylib", "libgstplayback.dylib",
                "libgsttypefindfunctions.dylib", "libgstlibav.dylib",
                "libgstavi.dylib", "libgstisomp4.dylib", "libgstmatroska.dylib",
                "libgstwavparse.dylib", "libgstvideoparsersbad.dylib",
                "libgstapplemedia.dylib", "libgstasf.dylib")
    for name in required:
        if not (runtime / "lib/gstreamer-1.0" / name).is_file():
            raise ValueError(f"Required media plugin was not built: {name}")

    for path in files:
        subprocess.run(["lipo", str(path), "-verify_arch", "x86_64"], check=True)
        is_library = path.suffix == ".dylib"
        if is_library:
            subprocess.run(["install_name_tool", "-id", "@rpath/" + path.name, str(path)], check=True)
        for line in run("otool", "-L", str(path)).splitlines()[1:]:
            dependency = line.strip().split(" (compatibility version ", 1)[0]
            if dependency.startswith(("/System/Library/", "/usr/lib/")):
                continue
            bundled = runtime / "lib" / Path(dependency).name
            if dependency == "@rpath/" + path.name and is_library:
                continue  # own install ID (plugins live in the plugin subdirectory)
            if not bundled.exists():
                raise ValueError(f"Unbundled media dependency in {path}: {dependency}")
            if Path(dependency).is_absolute() and not Path(dependency).is_relative_to(prefix):
                raise ValueError(f"External build dependency in {path}: {dependency}")
            subprocess.run(["install_name_tool", "-change", dependency,
                            "@rpath/" + bundled.name, str(path)], check=True)

        relative_libdir = "@loader_path"
        if path.parent == runtime / "lib/gstreamer-1.0":
            relative_libdir += "/.."
        elif path.parent == runtime / "libexec/gstreamer-1.0":
            relative_libdir += "/../../lib"
        existing = set(re.findall(r"\bpath (\S+) \(offset", run("otool", "-l", str(path))))
        if relative_libdir not in existing:
            subprocess.run(["install_name_tool", "-add_rpath", relative_libdir, str(path)], check=True)
        # install_name_tool invalidates the linker's ad-hoc signature. Restore
        # an ad-hoc signature without enabling Hardened Runtime.
        subprocess.run(["codesign", "--force", "--sign", "-", str(path)], check=True)
    print(f"Relocated {len(files)} source-built media Mach-O files, all x86_64")


if __name__ == "__main__":
    main()
