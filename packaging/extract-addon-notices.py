#!/usr/bin/env python3
"""Preserve upstream add-on notices without loading their DLLs or running Wine."""
import argparse
import io
from pathlib import Path, PurePosixPath
import re
import struct
import tarfile
import zipfile
import zlib


def gecko_notices(data):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        # Mozilla optimized JARs store the central directory immediately after
        # a four-byte readahead size. Parse just the two upstream license files.
        offset = 4
        while data[offset:offset + 4] == b"PK\x01\x02":
            entry = struct.unpack_from("<4s6H3I5H2I", data, offset)
            name = data[offset + 46:offset + 46 + entry[10]].decode("utf-8")
            offset += 46 + sum(entry[10:13])
            if "license" not in PurePosixPath(name).name.lower():
                continue
            local = entry[16]
            header = struct.unpack_from("<4s5H3I2H", data, local)
            if header[0] != b"PK\x03\x04":
                raise ValueError("Invalid Gecko JAR local header")
            start = local + 30 + header[9] + header[10]
            compressed = data[start:start + entry[8]]
            if entry[4] == 8:
                content = zlib.decompress(compressed, -15)
            elif entry[4] == 0:
                content = compressed
            else:
                raise ValueError("Unsupported Gecko JAR license compression")
            if len(content) != entry[9] or zlib.crc32(content) != entry[7]:
                raise ValueError("Gecko license size or CRC mismatch")
            yield name, content
    else:
        with archive:
            for name in archive.namelist():
                if "license" in PurePosixPath(name).name.lower():
                    yield name, archive.read(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mono_source", type=Path)
    parser.add_argument("gecko_jar", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    count = 0
    with tarfile.open(args.mono_source) as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            license_name = re.match(r"^(?:copying|licen[sc]e|notice|copyright)(?:$|[._-])", path.name, re.I)
            license_directory = any(part.lower() in ("licenses", "licences") for part in path.parts[:-1])
            sharpzip_notice = "ICSharpCode.SharpZipLib" in path.parts and path.name.startswith("README")
            if not member.isfile() or not (license_name or license_directory or sharpzip_notice):
                continue
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe Mono source notice path")
            destination = output / "mono-source" / Path(*path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.extractfile(member).read())
            count += 1
    if not count:
        raise ValueError("Mono source archive has no license notices")
    licenses = dict(gecko_notices(args.gecko_jar.read_bytes()))
    if "chrome/toolkit/content/global/license.html" not in licenses:
        raise ValueError("Gecko's complete about:license page is missing")
    for name, content in licenses.items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Unsafe Gecko license path")
        destination = output / "gecko" / Path(*path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    print(f"Preserved {count} Mono source notices and {len(licenses)} Gecko license pages")


if __name__ == "__main__":
    main()
