"""Read and rewrite Steam's local app metadata cache (appcache/appinfo.vdf).

The cache is what the client consults for an app's supported platforms, depots and
launch entries. Layout (v27..v29):

    u32 magic, u32 universe
    [v29] i64 offset of the key string table
    repeated entry:
        u32 appid                      (0 terminates the list)
        u32 size                       (bytes that follow, up to the next entry)
        u32 info_state, u32 last_updated, u64 access_token
        20  sha1 of the text-format appinfo
        u32 change_number
        [v28+] 20 sha1 of the binary blob
        binary KeyValues blob          (v29: keys are indices into the string table)
    [v29] u32 count, then `count` NUL-terminated key strings

Entries we do not touch are carried through byte-for-byte.
"""
from __future__ import annotations

import hashlib
import io
import os
import struct
from typing import Any, Dict, List, Optional

from . import vdf

MAGICS = vdf.APPINFO_MAGICS


class AppEntry:
    __slots__ = ("appid", "info_state", "last_updated", "token", "text_sha1", "change_number",
                 "binary_sha1", "blob", "_file")

    def __init__(self, file: "AppInfoFile"):
        self._file = file

    @property
    def kv(self) -> Dict[str, Any]:
        return vdf.binary_load(io.BytesIO(self.blob), self._file.strings)

    def set_kv(self, kv: Dict[str, Any], refresh_text_sha1: bool = False) -> None:
        """Replace this entry's contents and refresh the binary checksum.

        The text checksum is what the server reported for this change number; our
        formatter only reproduces it for a minority of entries, so it is left alone
        unless asked for.
        """
        self.blob = self._file.encode(kv)
        if self.binary_sha1 is not None:
            self.binary_sha1 = hashlib.sha1(self.blob).digest()
        if refresh_text_sha1:
            self.text_sha1 = text_sha1(kv)


class AppInfoFile:
    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as f:
            data = f.read()
        self.magic, self.universe = struct.unpack_from("<II", data, 0)
        self.version = MAGICS.get(self.magic)
        if self.version is None:
            raise vdf.VDFError("unsupported appinfo.vdf magic 0x%08x" % self.magic)
        pos = 8
        self.strings: Optional[List[str]] = None
        self._index: Dict[str, int] = {}
        end = len(data)
        if self.version >= 29:
            (table_off,) = struct.unpack_from("<q", data, pos)
            pos += 8
            (count,) = struct.unpack_from("<I", data, table_off)
            raw = data[table_off + 4:].split(b"\0")[:count]
            self.strings = [s.decode("utf-8", "replace") for s in raw]
            self._index = {s: i for i, s in enumerate(self.strings)}
            end = table_off
        self.entries: List[AppEntry] = []
        has_bin = self.version >= 28
        while pos + 4 <= end:
            (appid,) = struct.unpack_from("<I", data, pos)
            if appid == 0:
                break
            (size,) = struct.unpack_from("<I", data, pos + 4)
            body = data[pos + 8:pos + 8 + size]
            e = AppEntry(self)
            e.appid = appid
            e.info_state, e.last_updated, e.token = struct.unpack_from("<IIQ", body, 0)
            e.text_sha1 = body[16:36]
            (e.change_number,) = struct.unpack_from("<I", body, 36)
            e.binary_sha1 = body[40:60] if has_bin else None
            e.blob = body[60 if has_bin else 40:]
            self.entries.append(e)
            pos += 8 + size

    def get(self, appid: int) -> Optional[AppEntry]:
        for e in self.entries:
            if e.appid == appid:
                return e
        return None

    def _key(self, key: str) -> bytes:
        if self.strings is None:
            return key.encode("utf-8") + b"\0"
        i = self._index.get(key)
        if i is None:
            i = len(self.strings)
            self.strings.append(key)
            self._index[key] = i
        return struct.pack("<i", i)

    def encode(self, kv: Dict[str, Any]) -> bytes:
        out = bytearray()

        def emit(d: Dict[str, Any]) -> None:
            for k, v in d.items():
                if isinstance(v, dict):
                    out.append(vdf.T_NONE)
                    out.extend(self._key(k))
                    emit(v)
                elif isinstance(v, vdf.UInt64):
                    out.append(vdf.T_UINT64)
                    out.extend(self._key(k) + struct.pack("<Q", v))
                elif isinstance(v, vdf.Int64):
                    out.append(vdf.T_INT64)
                    out.extend(self._key(k) + struct.pack("<q", v))
                elif isinstance(v, vdf.Float32):
                    out.append(vdf.T_FLOAT)
                    out.extend(self._key(k) + struct.pack("<f", v))
                elif isinstance(v, int):
                    out.append(vdf.T_INT)
                    out.extend(self._key(k) + struct.pack("<I", v & 0xFFFFFFFF))
                else:
                    out.append(vdf.T_STR)
                    out.extend(self._key(k) + str(v).encode("utf-8") + b"\0")
            out.append(vdf.T_END)

        emit(kv)
        return bytes(out)

    def dumps(self) -> bytes:
        body = bytearray()
        for e in self.entries:
            head = struct.pack("<IIQ", e.info_state, e.last_updated, e.token) + e.text_sha1
            head += struct.pack("<I", e.change_number)
            if e.binary_sha1 is not None:
                head += e.binary_sha1
            body += struct.pack("<II", e.appid, len(head) + len(e.blob)) + head + e.blob
        body += struct.pack("<I", 0)
        out = bytearray(struct.pack("<II", self.magic, self.universe))
        if self.strings is not None:
            out += struct.pack("<q", 8 + 8 + len(body))
            out += body
            out += struct.pack("<I", len(self.strings))
            for s in self.strings:
                out += s.encode("utf-8") + b"\0"
        else:
            out += body
        return bytes(out)

    def save(self, path: Optional[str] = None) -> None:
        path = path or self.path
        tmp = path + ".gluon-tmp"
        with open(tmp, "wb") as f:
            f.write(self.dumps())
        os.replace(tmp, path)


def _text(d: Dict[str, Any], depth: int = 0) -> bytes:
    """Text KeyValues exactly as Steam formats them before hashing."""
    tabs = b"\t" * depth
    out = bytearray()
    for k, v in d.items():
        key = tabs + b'"' + str(k).replace("\\", "\\\\").encode("utf-8") + b'"'
        if isinstance(v, dict):
            out += key + b"\n" + tabs + b"{\n" + _text(v, depth + 1) + tabs + b"}\n"
        else:
            out += key + b'\t\t"' + str(v).replace("\\", "\\\\").encode("utf-8") + b'"\n'
    return bytes(out)


def text_sha1(kv: Dict[str, Any]) -> bytes:
    return hashlib.sha1(_text(kv)).digest()
