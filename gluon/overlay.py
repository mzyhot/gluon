"""Validate Steam's own native overlay libraries for a final Wine game launch.

This only reads Valve's installed files. It does not load, copy or modify them.
The launcher keeps dyld insertion disabled for prefix setup and helper commands.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import struct
from typing import Mapping, Optional

CPU_X86_64 = 0x01000007
MH_DYLIB = 6
ALLOWED = {"steamloader.dylib", "gameoverlayrenderer.dylib"}


def _x64_header(header: bytes) -> bool:
    endian = {b"\xcf\xfa\xed\xfe": "<", b"\xfe\xed\xfa\xcf": ">"}.get(header[:4])
    if endian is None or len(header) < 32:
        return False
    cpu, _, file_type = struct.unpack_from(endian + "III", header, 4)
    return cpu == CPU_X86_64 and file_type == MH_DYLIB


def has_x64_dylib(path: Path) -> bool:
    """Read only headers, accepting a thin x64 dylib or a valid universal slice."""
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            return False
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            if identity != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                return False
            header = stream.read(32)
            if _x64_header(header):
                valid = True
            else:
                fat = {b"\xca\xfe\xba\xbe": (">", False), b"\xbe\xba\xfe\xca": ("<", False),
                       b"\xca\xfe\xba\xbf": (">", True), b"\xbf\xba\xfe\xca": ("<", True)}.get(header[:4])
                if fat is None or len(header) < 8:
                    return False
                endian, wide = fat
                count = struct.unpack_from(endian + "I", header, 4)[0]
                entry_size = 32 if wide else 20
                table_end = 8 + count * entry_size
                if not 1 <= count <= 64 or table_end > info.st_size:
                    return False
                stream.seek(8)
                table = stream.read(count * entry_size)
                if len(table) != count * entry_size:
                    return False
                selected = []
                ranges = []
                for index in range(count):
                    at = index * entry_size
                    if wide:
                        cpu, _, offset, size, alignment, _ = struct.unpack_from(endian + "IIQQII", table, at)
                    else:
                        cpu, _, offset, size, alignment = struct.unpack_from(endian + "IIIII", table, at)
                    if (offset < table_end or size < 32 or offset + size > info.st_size
                            or alignment > 31 or offset % (1 << alignment)):
                        return False
                    if any(offset < end and start < offset + size for start, end in ranges):
                        return False
                    ranges.append((offset, offset + size))
                    if cpu == CPU_X86_64:
                        selected.append((offset, size))
                if len(selected) != 1:
                    return False
                stream.seek(selected[0][0])
                valid = _x64_header(stream.read(32))
            after = os.fstat(stream.fileno())
            named = path.lstat()
            return (valid and identity == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    == (named.st_dev, named.st_ino, named.st_size, named.st_mtime_ns))
    except (OSError, ValueError, struct.error):
        return False


def validated_insertions(environment: Mapping[str, str], client_dir: str) -> Optional[str]:
    """Return only Steam's supplied, client-local x64 overlay insertion list."""
    if environment.get("SteamClientLaunch") != "1":
        return None
    game_id = environment.get("SteamOverlayGameId", "")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", game_id) or int(game_id) > (1 << 64) - 1:
        return None
    raw = environment.get("STEAM_DYLD_INSERT_LIBRARIES", "")
    paths = raw.split(":")
    if not raw or len(paths) > 2 or any(ord(c) < 32 for c in raw + client_dir):
        return None
    try:
        client = Path(client_dir)
        if not client.is_absolute():
            return None
        client = client.resolve(strict=True)
        if not client.is_dir():
            return None
        result = []
        seen = set()
        for value in paths:
            path = Path(value)
            if not path.is_absolute() or path.name not in ALLOWED or path.name in seen:
                return None
            # Do not accept a Valve-named library from a different folder, or
            # a symbolic file that points outside the actual client directory.
            if path.is_symlink():
                return None
            resolved = path.resolve(strict=True)
            if resolved.parent != client or resolved.name != path.name or not has_x64_dylib(resolved):
                return None
            seen.add(path.name)
            result.append(str(resolved))
        if "gameoverlayrenderer.dylib" not in seen:
            return None
        return ":".join(result)
    except (OSError, ValueError, RuntimeError):
        return None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-dir", required=True)
    args = parser.parse_args(argv)
    value = validated_insertions(os.environ, args.client_dir)
    if value is not None:
        print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
