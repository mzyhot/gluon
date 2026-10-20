"""Select a packaged runtime, replacing an existing selection only while idle.

Packages stage a complete runtime under a unique name. Old runtimes and game
prefixes stay in place; only the default symlink changes, with a rollback record.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid
from typing import List

from . import session, steam


class RuntimeSelectionError(Exception):
    pass


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate runtime request key")
        result[key] = value
    return result


def _request(path: Path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise RuntimeSelectionError("cannot inspect packaged runtime request") from error
    if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
        raise RuntimeSelectionError("invalid packaged runtime request")
    try:
        with path.open(encoding="utf-8") as stream:
            request = json.load(stream, object_pairs_hook=_unique_keys)
    except (OSError, ValueError, RuntimeError) as error:
        raise RuntimeSelectionError("cannot read packaged runtime request") from error
    if (not isinstance(request, dict) or set(request) != {"schema", "name", "version"}
            or type(request["schema"]) is not int or request["schema"] != 1
            or not isinstance(request["name"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", request["name"])
            or request["name"] == "current"
            or not isinstance(request["version"], str)
            or not re.fullmatch(r"[0-9][A-Za-z0-9_.-]{0,63}", request["version"])):
        raise RuntimeSelectionError("invalid packaged runtime request")
    return request


def _current(path: Path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISLNK(info.st_mode):
        raise RuntimeSelectionError("runtime/current is not a symlink; it was left alone")
    return os.readlink(path)


def _idle(*, home=None, drain_warm=True) -> bool:
    if steam.is_running():
        return False
    try:
        result = subprocess.run(["pgrep", "-x", "wineserver"], capture_output=True, timeout=5)
        if result.returncode == 0 and drain_warm:
            # Only attested, user-idle Gluon servers can be asked to retire.
            # The global inventory remains authoritative for foreign servers,
            # active sessions and incomplete/unsupported idle requests.
            session.drain_warm_sessions(home or steam.GLUON_HOME)
            result = subprocess.run(["pgrep", "-x", "wineserver"], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeSelectionError("cannot check Wine activity; runtime selection was left alone") from error
    if result.returncode not in (0, 1):
        raise RuntimeSelectionError("cannot check Wine activity; runtime selection was left alone")
    return result.returncode == 1


def _can_activate(previous, *, home=None, drain_warm=True) -> bool:
    # A first selection only creates a missing link. It replaces no runtime that
    # Steam or Wine could already be using, so it need not wait for them to quit.
    return previous is None or _idle(home=home, drain_warm=drain_warm)


def activate_pending(dry_run: bool = False, home: str = None,
                     bootstrap_only: bool = False) -> List[str]:
    home_path = Path(home or steam.GLUON_HOME)
    runtime_dir = home_path / "runtime"
    pending = runtime_dir / "pending.json"
    request = _request(pending)
    if request is None:
        return []
    try:
        base = runtime_dir.resolve(strict=True)
        candidate = runtime_dir / request["name"]
        if candidate.is_symlink() or candidate.resolve(strict=True).parent != base:
            raise RuntimeSelectionError("packaged runtime must be a directory inside Gluon")
        if not candidate.is_dir() or not (candidate / "licenses").is_dir():
            raise RuntimeSelectionError("packaged runtime is incomplete")
        if not ((os.access(candidate / "bin/wine", os.X_OK)
                 and os.access(candidate / "bin/wineserver", os.X_OK))
                or (os.access(candidate / "proton", os.X_OK)
                    and os.access(candidate / "files/bin/wine", os.X_OK)
                    and os.access(candidate / "files/bin/wineserver", os.X_OK))):
            raise RuntimeSelectionError("packaged runtime has no complete Wine launcher and server")
        current = runtime_dir / "current"
        previous = _current(current)
        if bootstrap_only and previous is not None:
            return []
        if previous == request["name"]:
            return []
        if not _can_activate(previous, home=home_path, drain_warm=not dry_run):
            return []
        if dry_run:
            return ["would select packaged runtime " + request["name"]]

        # Recheck the request, selection and process state immediately before
        # publishing. A concurrent install or Steam launch defers replacement.
        if (_request(pending) != request or _current(current) != previous
                or not _can_activate(previous, home=home_path, drain_warm=not dry_run)):
            return []
        token = uuid.uuid4().hex
        backup = home_path / "backups" / ("runtime-" + token)
        backup.mkdir(parents=True, exist_ok=False)
        with (backup / "selection.json").open("x", encoding="utf-8") as stream:
            json.dump({"previous": previous, "selected": request["name"],
                       "version": request["version"]}, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary = runtime_dir / (".current-" + token)
        try:
            temporary.symlink_to(request["name"])
            # The complete old directory stays intact for rollback and never
            # receives files from the replacement runtime.
            if (_request(pending) != request or _current(current) != previous
                    or not _can_activate(previous, home=home_path, drain_warm=not dry_run)):
                return []
            if previous is None:
                try:
                    # Do not replace a selection created after the final check,
                    # including a dangling symlink or a non-symlink entry.
                    current.symlink_to(request["name"])
                except FileExistsError:
                    return []
            else:
                os.replace(temporary, current)
        finally:
            if temporary.is_symlink():
                temporary.unlink()
        return ["selected packaged runtime " + request["name"]]
    except (OSError, ValueError) as error:
        raise RuntimeSelectionError("cannot select the packaged runtime; previous runtime retained") from error


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create a missing packaged runtime selection")
    parser.add_argument("--bootstrap", action="store_true", required=True,
                        help="create runtime/current only when it does not exist")
    parser.add_argument("--home", help="Gluon data folder")
    args = parser.parse_args(argv)
    try:
        for line in activate_pending(home=args.home, bootstrap_only=True):
            print(line)
    except RuntimeSelectionError as error:
        print("gluon: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
