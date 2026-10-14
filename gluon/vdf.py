"""Valve KeyValues readers/writers.

Three on-disk dialects matter to us:

* text VDF      - config.vdf, libraryfolders.vdf, appmanifest_*.acf, toolmanifest.vdf
* binary VDF    - shortcuts.vdf, and the per-app blobs inside appinfo.vdf
* appinfo.vdf   - a container of binary VDF blobs (v27..v29, v29 adds a string table)

Everything is parsed into plain dicts (insertion ordered) so callers can edit and
write back without losing sibling keys they don't understand.
"""
from __future__ import annotations

import io
import struct
from typing import Any, BinaryIO, Dict, Iterator, List, Optional, Tuple

# --------------------------------------------------------------------------- text


class VDFError(ValueError):
    pass


_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}
_UNESCAPES = {"\n": "\\n", "\t": "\\t", "\\": "\\\\", '"': '\\"'}


def _tokens(text: str) -> Iterator[Tuple[str, str]]:
    """Yield (kind, value) where kind is 'str', '{' or '}'."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif c in "{}":
            yield c, c
            i += 1
        elif c == '"':
            i += 1
            out = []
            while True:
                if i >= n:
                    raise VDFError("unterminated string")
                c = text[i]
                if c == "\\" and i + 1 < n and text[i + 1] in _ESCAPES:
                    out.append(_ESCAPES[text[i + 1]])
                    i += 2
                elif c == '"':
                    i += 1
                    break
                else:
                    out.append(c)
                    i += 1
            yield "str", "".join(out)
        elif c == "[":
            # platform conditional such as [$WIN32] - irrelevant for the files we read
            j = text.find("]", i)
            i = n if j < 0 else j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n{}"':
                j += 1
            yield "str", text[i:j]
            i = j


def loads(text: str) -> Dict[str, Any]:
    if text.startswith("﻿"):
        text = text[1:]
    root: Dict[str, Any] = {}
    stack = [root]
    key: Optional[str] = None
    for kind, val in _tokens(text):
        if kind == "str":
            if key is None:
                key = val
            else:
                stack[-1][key] = val
                key = None
        elif kind == "{":
            if key is None:
                raise VDFError("section without a name")
            child = stack[-1].get(key)
            if not isinstance(child, dict):
                child = {}
                stack[-1][key] = child
            stack.append(child)
            key = None
        else:
            if len(stack) == 1:
                raise VDFError("unbalanced '}'")
            stack.pop()
    if len(stack) != 1:
        raise VDFError("unbalanced '{'")
    return root


def _q(s: Any) -> str:
    return '"' + "".join(_UNESCAPES.get(c, c) for c in str(s)) + '"'


def dumps(data: Dict[str, Any], indent: int = 0) -> str:
    out: List[str] = []
    pad = "\t" * indent
    for k, v in data.items():
        if isinstance(v, dict):
            out.append("%s%s\n%s{\n%s%s}\n" % (pad, _q(k), pad, dumps(v, indent + 1), pad))
        else:
            out.append("%s%s\t\t%s\n" % (pad, _q(k), _q(v)))
    return "".join(out)


def load(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return loads(f.read())


def dump(data: Dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(data))


def iget(d: Any, *path: str, default: Any = None) -> Any:
    """Case-insensitive nested lookup - Steam is inconsistent about key casing."""
    cur = d
    for part in path:
        if not isinstance(cur, dict):
            return default
        if part in cur:
            cur = cur[part]
            continue
        lower = part.lower()
        for k in cur:
            if k.lower() == lower:
                cur = cur[k]
                break
        else:
            return default
    return cur


def isetdefault(d: Dict[str, Any], key: str) -> Dict[str, Any]:
    """Return the child section `key` (matched case-insensitively), creating it if absent."""
    lower = key.lower()
    for k, v in d.items():
        if k.lower() == lower and isinstance(v, dict):
            return v
    d[key] = {}
    return d[key]


# ------------------------------------------------------------------------- binary

T_NONE, T_STR, T_INT, T_FLOAT, T_PTR, T_WSTR, T_COLOR, T_UINT64, T_END, T_INT64, T_END_ALT = (
    0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11)


class UInt64(int):
    """Marker so 64-bit values round-trip with their original type tag."""


class Int64(int):
    pass


class Float32(float):
    pass


def _cstr(f: BinaryIO) -> str:
    buf = bytearray()
    while True:
        chunk = f.read(64)
        if not chunk:
            raise VDFError("eof inside string")
        z = chunk.find(b"\0")
        if z >= 0:
            buf += chunk[:z]
            f.seek(z + 1 - len(chunk), io.SEEK_CUR)
            return buf.decode("utf-8", "replace")
        buf += chunk


def binary_load(f: BinaryIO, strings: Optional[List[str]] = None) -> Dict[str, Any]:
    """Read one binary KeyValues object. `strings` is appinfo v29's key table."""
    root: Dict[str, Any] = {}
    stack = [root]
    while True:
        t = f.read(1)
        if not t:
            raise VDFError("eof inside binary vdf")
        t = t[0]
        if t in (T_END, T_END_ALT):
            stack.pop()
            if not stack:
                return root
            continue
        if strings is not None:
            key = strings[struct.unpack("<i", f.read(4))[0]]
        else:
            key = _cstr(f)
        cur = stack[-1]
        if t == T_NONE:
            child: Dict[str, Any] = {}
            cur[key] = child
            stack.append(child)
        elif t == T_STR:
            cur[key] = _cstr(f)
        elif t in (T_INT, T_PTR, T_COLOR):
            cur[key] = struct.unpack("<i", f.read(4))[0]
        elif t == T_FLOAT:
            cur[key] = Float32(struct.unpack("<f", f.read(4))[0])
        elif t == T_UINT64:
            cur[key] = UInt64(struct.unpack("<Q", f.read(8))[0])
        elif t == T_INT64:
            cur[key] = Int64(struct.unpack("<q", f.read(8))[0])
        elif t == T_WSTR:
            raise VDFError("wide strings are not supported")
        else:
            raise VDFError("unknown binary vdf type %d" % t)


def binary_loads(data: bytes) -> Dict[str, Any]:
    return binary_load(io.BytesIO(data))


def binary_dumps(data: Dict[str, Any]) -> bytes:
    out = bytearray()

    def emit(d: Dict[str, Any]) -> None:
        for k, v in d.items():
            kb = str(k).encode("utf-8") + b"\0"
            if isinstance(v, dict):
                out.append(T_NONE)
                out.extend(kb)
                emit(v)
            elif isinstance(v, UInt64):
                out.append(T_UINT64)
                out.extend(kb)
                out.extend(struct.pack("<Q", v))
            elif isinstance(v, Int64):
                out.append(T_INT64)
                out.extend(kb)
                out.extend(struct.pack("<q", v))
            elif isinstance(v, bool):
                out.append(T_INT)
                out.extend(kb)
                out.extend(struct.pack("<i", int(v)))
            elif isinstance(v, int):
                out.append(T_INT)
                out.extend(kb)
                # shortcut appids are stored as signed 32 bit; accept either spelling
                out.extend(struct.pack("<I", v & 0xFFFFFFFF))
            elif isinstance(v, float):
                out.append(T_FLOAT)
                out.extend(kb)
                out.extend(struct.pack("<f", v))
            else:
                out.append(T_STR)
                out.extend(kb)
                out.extend(str(v).encode("utf-8") + b"\0")
        out.append(T_END)

    emit(data)
    return bytes(out)


# ------------------------------------------------------------------------ appinfo

APPINFO_MAGICS = {0x07564427: 27, 0x07564428: 28, 0x07564429: 29}


def appinfo_iter(path: str) -> Iterator[Tuple[int, Dict[str, Any]]]:
    """Yield (appid, keyvalues) for every app Steam has cached metadata for."""
    with open(path, "rb") as f:
        magic, _universe = struct.unpack("<II", f.read(8))
        version = APPINFO_MAGICS.get(magic)
        if version is None:
            raise VDFError("unsupported appinfo.vdf magic 0x%08x" % magic)
        strings: Optional[List[str]] = None
        if version >= 29:
            (table_off,) = struct.unpack("<q", f.read(8))
            resume = f.tell()
            f.seek(table_off)
            (count,) = struct.unpack("<I", f.read(4))
            blob = f.read()
            strings = [s.decode("utf-8", "replace") for s in blob.split(b"\0")[:count]]
            f.seek(resume)
        # appid, size, infostate, last_updated, token, sha1, changenumber[, binary sha1]
        skip = 4 + 4 + 8 + 20 + 4 + (20 if version >= 28 else 0)
        while True:
            head = f.read(8)
            if len(head) < 8:
                return
            appid, size = struct.unpack("<II", head)
            if appid == 0:
                return
            body = f.read(size)
            try:
                yield appid, binary_load(io.BytesIO(body[skip:]), strings)
            except (VDFError, struct.error, IndexError):
                continue


PACKAGEINFO_MAGICS = {0x06565527: 39, 0x06565528: 40}


def packageinfo_iter(path: str) -> Iterator[Tuple[int, Dict[str, Any]]]:
    """Yield (packageid, keyvalues) for every package the local account has loaded."""
    with open(path, "rb") as f:
        magic, _universe = struct.unpack("<II", f.read(8))
        version = PACKAGEINFO_MAGICS.get(magic)
        if version is None:
            raise VDFError("unsupported packageinfo.vdf magic 0x%08x" % magic)
        while True:
            head = f.read(4)
            if len(head) < 4:
                return
            (pkgid,) = struct.unpack("<I", head)
            if pkgid == 0xFFFFFFFF:
                return
            f.seek(20 + 4 + (8 if version >= 40 else 0), io.SEEK_CUR)
            yield pkgid, binary_load(f)
