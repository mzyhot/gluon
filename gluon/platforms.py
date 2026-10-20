"""Reuse the packaged native classifier for installer Cloud eligibility."""
from __future__ import annotations

import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time

MAX_OUTPUT = 1024 * 1024
HELPER_TIMEOUT = 15.0
_KEYS = (b"mac", b"windows", b"mac32", b"windows_launch_only")
_IDS = re.compile(rb"[1-9][0-9]{0,9}(?:,[1-9][0-9]{0,9})*")


def _stamp(path):
    value = path.stat()
    if not stat.S_ISREG(value.st_mode):
        raise ValueError("classifier input must be an ordinary file")
    return (value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _mac32_enabled(home):
    path = Path(home) / "steamplay/auto-mac32"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            return (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and os.read(fd, 3) == b"1\n")
        finally:
            os.close(fd)
    except OSError:
        return False


def _catalogue(output):
    """Accept only the exact four-line native helper protocol."""
    if not isinstance(output, bytes) or len(output) > MAX_OUTPUT:
        raise ValueError("invalid classifier output")
    lines = output.split(b"\n")
    if len(lines) != 5 or lines[-1]:
        raise ValueError("incomplete classifier catalogue")
    result = {}
    for key, line in zip(_KEYS, lines):
        name, equal, values = line.partition(b"=")
        if name != key or equal != b"=" or (values and not _IDS.fullmatch(values)):
            raise ValueError("invalid classifier list")
        ids = values.decode("ascii").split(",") if values else []
        if len(set(ids)) != len(ids) or any(int(appid) > 0xFFFFFFFF for appid in ids):
            raise ValueError("ambiguous classifier list")
        result[key] = set(ids)
    common = result[b"mac"] & result[b"windows"]
    if (not result[b"mac32"] <= common
            or not result[b"windows_launch_only"] <= common
            or result[b"mac32"] & result[b"windows_launch_only"]):
        raise ValueError("conflicting classifier evidence")
    return result


def _run_packaged_helper(helper, source, environment):
    """Bound time and captured bytes while running only the packaged reader."""
    process = subprocess.Popen([str(helper), str(source), "--platforms"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env=environment,
                               close_fds=True)
    output = bytearray()
    deadline = time.monotonic() + HELPER_TIMEOUT
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    return None
                block = os.read(process.stdout.fileno(), 65536)
                if not block:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    return bytes(output) if process.wait(timeout=remaining) == 0 else None
                if len(output) + len(block) > MAX_OUTPUT:
                    return None
                output.extend(block)
    finally:
        try:
            if process.poll() is None:
                process.kill()
        finally:
            try:
                process.wait()
            finally:
                process.stdout.close()


def automatic_mac_candidates(installation) -> set[str]:
    """Return additional Mac-advertised apps eligible for automatic Gluon.

    The install caller has already verified this selected Steam Play payload.
    Missing, changed or malformed evidence never broadens eligibility. The
    existing native Cloud guard can retry safely when Steam later resolves Play.
    Explicit per-game/global choices remain the install caller's responsibility.
    """
    helper = Path(installation.payload) / "appinfo"
    source = Path(installation.root) / "appcache/appinfo.vdf"
    try:
        if helper.is_symlink() or not os.access(helper, os.X_OK):
            return set()
        before = (_stamp(helper), _stamp(source))
        enabled = _mac32_enabled(installation.home)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("DYLD_", "STEAM_DYLD_", "LD_"))}
        environment.update(GLUON_HOME=str(installation.home),
                           GLUON_STEAM_ROOT=str(installation.root),
                           STEAM_ROOT=str(installation.root),
                           PATH="/usr/bin:/bin:/usr/sbin:/sbin")
        output = _run_packaged_helper(helper, source, environment)
        if (before != (_stamp(helper), _stamp(source))
                or enabled != _mac32_enabled(installation.home)):
            return set()
        catalogue = _catalogue(output)
        return catalogue[b"windows_launch_only"] | (catalogue[b"mac32"] if enabled else set())
    except (OSError, ValueError, subprocess.SubprocessError):
        return set()
