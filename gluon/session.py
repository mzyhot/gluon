"""Own prefix leases, bounded warm sessions and confirmed Wine shutdown."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import signal
import subprocess
import sys
import time

def warm_policy_allows(runtime, prefix, *, environment=None):
    # Lease-only operations also work with installations predating this module.
    from .service_policy import warm_policy_allows as allows
    return allows(runtime, prefix, environment=environment)


def _reset_child_signals():
    # This CLI is single-threaded. The launcher deliberately ignores repeated
    # stop requests during cleanup; do not pass that state into wineserver.
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_SETMASK, [])


def _run_helper(wineserver: str, argument: str, timeout: float, *, prefix=None, neutral=False):
    """Return the helper's exit status, or None if it could not start or timed out."""
    try:
        environment = None
        if neutral:
            environment = _system_environment(Path(wineserver).parent.parent, prefix)
        elif prefix is not None:
            environment = dict(os.environ, WINEPREFIX=str(prefix))
        child = subprocess.Popen([wineserver, argument], env=environment,
                                 preexec_fn=_reset_child_signals, close_fds=True)
    except OSError as error:
        print("gluon: cannot start wineserver %s: %s" % (argument, error), file=sys.stderr, flush=True)
        return None
    try:
        return child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print("gluon: wineserver %s timed out after %gs; ending its shutdown helper" %
              (argument, timeout), file=sys.stderr, flush=True)
        child.kill()
        child.wait()
        return None


def stop_prefix(wineserver: str, timeout: float = 5.0) -> bool:
    """Run -k and -w under the inherited WINEPREFIX, each with its own deadline.

    `wineserver -k` exits 1 both when no server is running and when it could not
    signal one, so only a -w that returns in time confirms the prefix stopped.
    Only this process's direct shutdown helpers are killed on timeout. Keeping
    child ownership through kill/wait prevents signaling a subsequently reused
    PID; no Wine process discovery or process-group signaling is involved.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("shutdown timeout must be finite and positive")
    status = _run_helper(wineserver, "-k", timeout)
    if status:
        print("gluon: wineserver -k exited with status %s (no running server, or it could not "
              "be signalled); confirming with -w" % status, file=sys.stderr, flush=True)
    status = _run_helper(wineserver, "-w", timeout)
    if status:
        print("gluon: wineserver -w failed (exit status %s)" % status, file=sys.stderr, flush=True)
    if status != 0:
        print("gluon: prefix shutdown could not be confirmed", file=sys.stderr, flush=True)
        return False
    return True



WARM_PROTOCOL = 1
WARM_SECONDS = 120
WARM_PATCH = "wine-session-idle"
LEASE_NAME = ".gluon-session.lock"


class SessionError(RuntimeError):
    pass


def _regular_owned(path, *, maximum=65536):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or info.st_size > maximum or info.st_mode & 0o077):
        raise SessionError("untrusted warm-session file: " + str(path))
    return info


def acquire_lease(fd: int, compat):
    """Lock the shell's shared open-file description, retained until shell exit.

    The shell closes this descriptor in every long-lived child. flock remains
    owned by the shell after this short Python helper exits, including on Stop.
    """
    path = Path(compat).resolve(strict=True) / LEASE_NAME
    info, opened = path.lstat(), os.fstat(fd)
    if (path.is_symlink() or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid() or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino)):
        raise SessionError("unsafe prefix session lease")
    os.fchmod(fd, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)


def _runtime_identity(runtime):
    runtime = Path(runtime).resolve(strict=True)
    applied = runtime / "licenses/gluon-wine-patches/APPLIED.txt"
    if (not applied.is_file() or applied.stat().st_size > 65536
            or WARM_PATCH not in applied.read_text().splitlines()):
        raise SessionError("runtime lacks Gluon's warm-session protocol")
    binaries = {}
    for name in ("wine", "wineserver"):
        path = runtime / "bin" / name
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK):
            raise SessionError("runtime has no executable " + name)
        binaries[name] = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]
    build = runtime / ".gluon-runtime-build-key"
    patches = runtime / ".gluon-wine-patches"
    keys = {}
    for name, path in (("build", build), ("patches", patches)):
        if not path.is_file() or path.stat().st_size > 4096:
            raise SessionError("runtime lacks its build attestation")
        keys[name] = path.read_text().strip()
        if not keys[name]:
            raise SessionError("empty runtime build attestation")
    return {"path": str(runtime), "binaries": binaries, **keys}


def _identity(prefix, runtime, *, msync=None):
    prefix = Path(prefix).resolve(strict=True)
    info = prefix.stat()
    if not prefix.is_dir() or info.st_uid != os.getuid():
        raise SessionError("prefix is not an owned directory")
    return {"prefix": str(prefix), "prefix_inode": [info.st_dev, info.st_ino],
            "runtime": _runtime_identity(runtime), "msync": os.environ.get("WINEMSYNC", "1") if msync is None else msync}


def _state_path(home, prefix):
    key = hashlib.sha256(str(Path(prefix).resolve()).encode()).hexdigest()
    return Path(home) / "state/warm-sessions" / (key + ".json")


def _read_state(path):
    try:
        _regular_owned(path)
    except FileNotFoundError:
        return None
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise SessionError("cannot read warm-session state") from error
    if isinstance(state, dict) and set(state) == {"schema", "seconds", "identity"}:
        # The initial implementation did not distinguish crash recovery from a
        # completed neutral bootstrap. Treat its attestation conservatively.
        state["phase"] = "starting"
    if (not isinstance(state, dict) or set(state) != {"schema", "seconds", "identity", "phase"}
            or type(state["schema"]) is not int or state["schema"] != WARM_PROTOCOL
            or type(state["seconds"]) is not int or state["seconds"] != WARM_SECONDS
            or not isinstance(state["identity"], dict)
            or state["phase"] not in ("starting", "ready")):
        raise SessionError("invalid warm-session state")
    identity = state["identity"]
    if (set(identity) != {"prefix", "prefix_inode", "runtime", "msync"}
            or not isinstance(identity["prefix"], str) or not Path(identity["prefix"]).is_absolute()
            or not isinstance(identity["prefix_inode"], list) or len(identity["prefix_inode"]) != 2
            or any(type(value) is not int or value < 0 for value in identity["prefix_inode"])
            or not isinstance(identity["msync"], str) or len(identity["msync"]) > 64
            or not isinstance(identity["runtime"], dict)
            or not isinstance(identity["runtime"].get("path"), str)
            or not Path(identity["runtime"]["path"]).is_absolute()):
        raise SessionError("invalid warm-session identity")
    return state


def _write_state(path, identity, *, phase="ready"):
    if phase not in ("starting", "ready"):
        raise ValueError("invalid warm-session phase")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({"schema": WARM_PROTOCOL, "seconds": WARM_SECONDS,
                       "identity": identity, "phase": phase}, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _drain(wineserver, prefix, timeout=5.0):
    # The server makes the idle decision atomically. Never replace this with
    # --wait-idle followed by -k: a new user process could appear in between.
    deadline = time.monotonic() + timeout
    result = _run_helper(str(wineserver), "--kill-idle", timeout, prefix=prefix)
    remaining = deadline - time.monotonic()
    if result not in (0, 3) or remaining <= 0:
        return False
    return _run_helper(str(wineserver), "-w", remaining, prefix=prefix) == 0


def drain_warm_sessions(home=None) -> bool:
    """Drain only attested, unleased Gluon warm prefixes before migrations.

    Unknown/corrupt records, live launch leases and busy servers return False.
    Callers keep their existing global Wine-activity check as the final guard.
    No process names are used as authority to terminate a prefix.
    """
    home = Path(home or os.environ.get("GLUON_HOME", str(Path.home() / "Library/Application Support/Gluon")))
    directory = home / "state/warm-sessions"
    if not directory.exists():
        return True
    paths = list(directory.glob("*.json"))
    if len(paths) > 4096:
        return False
    success = True
    deadline = time.monotonic() + 8.0
    for path in paths:
        if time.monotonic() >= deadline:
            return False
        lease_fd = None
        try:
            state = _read_state(path)
            identity = state["identity"]
            prefix = Path(identity["prefix"])
            if (not prefix.is_absolute() or path != _state_path(home, prefix)
                    or identity != _identity(prefix, identity["runtime"]["path"], msync=identity["msync"])):
                raise SessionError("warm-session identity changed")
            lease = prefix.parent / LEASE_NAME
            info = _regular_owned(lease, maximum=0)
            lease_fd = os.open(lease, os.O_WRONLY | os.O_NOFOLLOW)
            opened = os.fstat(lease_fd)
            if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                raise SessionError("warm-session lease changed")
            fcntl.flock(lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            server = Path(identity["runtime"]["path"]) / "bin/wineserver"
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not _drain(server, prefix, timeout=min(5.0, remaining)):
                success = False
                continue
            # Full -w confirmed the server gone under the same prefix lease.
            # Prune only this exact record; no live warm fingerprint is needed.
            if _read_state(path) == state:
                path.unlink()
            else:
                success = False
        except (KeyError, TypeError, OSError, ValueError, SessionError):
            success = False
        finally:
            if lease_fd is not None:
                os.close(lease_fd)
    return success



def _system_environment(runtime, prefix):
    # Retained Wine server/services outlive a game. Neither may carry Steam's
    # game ownership, a Game.app loader path, or native overlay insertion.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("Steam", "STEAM_", "GLUON_", "DYLD_"))
                   and key not in ("WINEDLLPATH", "WINEBUILDDIR", "WINEPATH", "WINEDLLOVERRIDES")}
    environment.update(WINEPREFIX=str(prefix), WINELOADER=str(Path(runtime) / "bin/wine"))
    environment['WINEDLLOVERRIDES'] = 'winemenubuilder.exe=d;mscoree,mshtml=;gluon-session-bootstrap.exe=b'
    return environment


def _bootstrap_services(runtime, prefix, timeout=30.0):
    environment = _system_environment(runtime, prefix)
    # The owned GUI bootstrap has no command parser, autorun or visible UI.
    command = Path(runtime) / "lib/wine/x86_64-windows/gluon-session-bootstrap.exe"
    if not command.is_file():
        raise SessionError("runtime lacks its builtin Wine bootstrap command")
    try:
        child = subprocess.Popen([str(Path(runtime) / "bin/wine"), str(command)],
                                 env=environment, preexec_fn=_reset_child_signals, close_fds=True)
    except OSError as error:
        raise SessionError("cannot bootstrap Wine services") from error
    try:
        if child.wait(timeout=timeout) != 0:
            raise SessionError("Wine service bootstrap failed")
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
        raise SessionError("Wine service bootstrap timed out")

def prepare_warm(home, prefix, runtime, *, enabled=True):
    """Run under the shell-held prefix lease; return with compatible idle state."""
    identity = _identity(prefix, runtime)
    enabled = enabled and warm_policy_allows(runtime, prefix, environment=_system_environment(runtime, prefix))
    state_path = _state_path(home, prefix)
    state = _read_state(state_path)
    if state is not None and state["identity"]["prefix"] != identity["prefix"]:
        raise SessionError("warm-session prefix identity changed")
    server = str(Path(identity["runtime"]["path"]) / "bin/wineserver")
    if state is None:
        # No attestation: an ordinary Wine session must finish normally.
        if _run_helper(server, "-w", WARM_SECONDS + 15, prefix=prefix) != 0:
            raise SessionError("previous Wine session did not finish")
    elif state["identity"] != identity or not enabled:
        # A runtime/settings change cannot attach a new client to an old warm
        # server. Drain it through the idle-only server-side decision first.
        if _run_helper(server, "--check-running", 5.0, prefix=prefix) != 3:
            old = state["identity"]
            if old != _identity(old["prefix"], old["runtime"]["path"], msync=old["msync"]):
                raise SessionError("previous warm-session runtime changed")
            old_server = Path(old["runtime"]["path"]) / "bin/wineserver"
            if state["identity"] == identity and not enabled:
                if (_run_helper(str(old_server), '--cold-session', 5.0, prefix=prefix) not in (0, 3)
                        or _run_helper(str(old_server), '-w', 15.0, prefix=prefix) != 0):
                    raise SessionError('previous Wine services did not finish')
            elif not _drain(old_server, prefix):
                raise SessionError("previous Wine session is busy")
    else:
        result = _run_helper(server, "--wait-idle", WARM_SECONDS + 15, prefix=prefix)
        if result == 3:
            if _run_helper(server, "-w", WARM_SECONDS + 15, prefix=prefix) != 0:
                raise SessionError("previous Wine session did not finish")
        elif result != 0:
            raise SessionError("cannot confirm Wine user processes are idle")
        elif state["phase"] == "ready":
            # The matching live server was already bootstrapped neutrally.
            # Avoid redundant server spawns, second idle checks and state I/O.
            # If it expires before the game starts, the final --wait-idle
            # falls back to a full ordinary server wait.
            return True
    if not enabled:
        state_path.unlink(missing_ok=True)
        return False
    # Publish recovery authority before creating a daemon. A crash or failed
    # bootstrap must never leave an anonymous warm server imposing a 120s wait.
    _write_state(state_path, identity, phase="starting")
    status = _run_helper(server, "--gluon-session=" + str(WARM_SECONDS), 5.0, prefix=prefix, neutral=True)
    if status not in (0, 2):
        raise SessionError("cannot start bounded Wine warm session")
    try:
        # A starting record has recovery authority but no proof that retained
        # services were initialized without the game's identity. Retry even
        # when the previous interrupted attempt left a running server.
        _bootstrap_services(identity["runtime"]["path"], prefix)
        if _run_helper(server, "--wait-idle", 5.0, prefix=prefix) != 0:
            raise SessionError("Wine warm-session capability was not confirmed")
        if not warm_policy_allows(runtime, prefix, environment=_system_environment(runtime, prefix)):
            if _run_helper(server, '--cold-session', 5.0, prefix=prefix) not in (0, 3):
                raise SessionError('cannot retire unverified Wine services')
            if _run_helper(server, '-w', 15.0, prefix=prefix) != 0:
                raise SessionError('unverified Wine services did not finish')
            state_path.unlink(missing_ok=True)
            return False
        if _run_helper(server, '--seal-session', 5.0, prefix=prefix) != 0:
            # A late service start or image load may have revoked eligibility.
            if _run_helper(server, '--cold-session', 5.0, prefix=prefix) not in (0, 3):
                raise SessionError('cannot retire changed Wine services')
            if _run_helper(server, '-w', 15.0, prefix=prefix) != 0:
                raise SessionError('changed Wine services did not finish')
            state_path.unlink(missing_ok=True)
            return False
        _write_state(state_path, identity, phase="ready")
        return True
    except Exception:
        # We may clean up a newly created idle server. A pre-existing server
        # belongs to its previous session; our failure never tears that down.
        if status == 0:
            try:
                if _drain(server, prefix):
                    saved = _read_state(state_path)
                    if saved is not None and saved["identity"] == identity:
                        state_path.unlink()
            except (OSError, ValueError, SessionError):
                pass  # Retain the attestation for the next safe drain attempt.
        raise


def check_cloud_after_lease(appid, compat, expected_mode):
    from . import cloud, steamplay
    try:
        mode = cloud.check_app(appid, compat)
        if mode != expected_mode:
            raise cloud.CloudError("Cloud save paths changed while this launch waited; retry Play")
    except (cloud.CloudError, steamplay.SteamPlayError) as error:
        raise SessionError("Cloud validation before Wine launch: " + str(error)) from error

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wineserver")
    parser.add_argument("--lease-fd", type=int)
    parser.add_argument("--compat")
    parser.add_argument("--check-cloud", metavar="APPID")
    parser.add_argument("--cloud-mode", choices=("native", "prefix"))
    parser.add_argument("--prepare-warm", action="store_true")
    parser.add_argument("--cold", action="store_true")
    parser.add_argument("--home")
    parser.add_argument("--prefix")
    parser.add_argument("--runtime")
    args = parser.parse_args(argv)
    try:
        if args.lease_fd is not None:
            if not args.compat:
                parser.error("--lease-fd requires --compat")
            acquire_lease(args.lease_fd, args.compat)
            if args.check_cloud:
                if not args.cloud_mode:
                    parser.error("--check-cloud requires --cloud-mode")
                check_cloud_after_lease(args.check_cloud, args.compat, args.cloud_mode)
            return 0
        if args.prepare_warm:
            if not all((args.home, args.prefix, args.runtime)):
                parser.error("--prepare-warm requires --home, --prefix and --runtime")
            warm = prepare_warm(args.home, args.prefix, args.runtime, enabled=not args.cold)
            return 0 if warm else 10  # Internal launcher protocol: valid ordinary cold fallback.
        if not args.wineserver:
            parser.error("--wineserver is required for Stop")
        return 0 if stop_prefix(args.wineserver) else 1
    except (OSError, ValueError, SessionError) as error:
        print("gluon: " + str(error), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
