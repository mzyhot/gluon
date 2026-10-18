"""Fetch Valve install-script helpers on the user's machine, never redistribute them.

Only files listed in the pinned upstream manifest are extracted. Both the archive
and each member must pass the upstream SHA-256 check before anything is installed.
"""
from __future__ import annotations
import hashlib
import io
from pathlib import Path
import urllib.request
import zipfile

from .steamplay import Installation, SteamPlayError, _atomic, _digest


def ensure():
    installation = Installation()
    # Running during a Steam launch is intended: this writes only Gluon's files.
    manifest = installation.payload / 'valve-packages.manifest'
    bases, packages, files = [], {}, []
    for line in manifest.read_text().splitlines():
        fields = line.split()
        if not fields:
            continue
        if fields[0] == 'base' and len(fields) == 2 and fields[1].startswith('https://'):
            bases.append(fields[1])
        elif fields[0] == 'package' and len(fields) == 4:
            packages[fields[1]] = fields[2:]
        elif fields[0] == 'file' and len(fields) == 5 and fields[1].startswith('legacycompat/'):
            files.append(fields[1:])
    required = {'legacycompat/Steam.dll', 'legacycompat/iscriptevaluator.exe', 'legacycompat/SteamService.exe'}
    if not required.issubset({x[0] for x in files}):
        raise SteamPlayError('incomplete Valve helper manifest')
    archives = {}
    for destination, package, member, digest in files:
        if Path(destination).is_absolute() or '..' in Path(destination).parts:
            raise SteamPlayError('unsafe Valve helper path')
        target = installation.home / 'steamplay' / destination
        if target.is_file() and not target.is_symlink() and _digest(target) == digest:
            continue
        if package not in archives:
            filename, archive_digest = packages[package]
            data = None
            for base in bases:
                try:
                    with urllib.request.urlopen(base + '/' + filename, timeout=60) as response:
                        candidate = response.read(256 * 1024 * 1024 + 1)
                    if len(candidate) <= 256 * 1024 * 1024 and hashlib.sha256(candidate).hexdigest() == archive_digest:
                        data = candidate
                        break
                except OSError:
                    continue
            if data is None:
                raise SteamPlayError('Valve helper download failed verification: ' + package)
            archives[package] = zipfile.ZipFile(io.BytesIO(data))
        archive = archives[package]
        info = archive.getinfo(member)
        if info.file_size > 64 * 1024 * 1024:
            raise SteamPlayError('oversized Valve helper')
        data = archive.read(info)
        if hashlib.sha256(data).hexdigest() != digest:
            raise SteamPlayError('Valve helper member failed verification: ' + member)
        _atomic(target, data)


if __name__ == '__main__':
    import sys
    try:
        ensure()
    except (SteamPlayError, OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print('gluon: ' + str(error), file=sys.stderr)
        sys.exit(1)
