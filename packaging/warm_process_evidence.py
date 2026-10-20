"""CI-only evidence for detached, identity-free retained Wine services.

This module never discovers or inspects processes outside a GitHub Actions Mac.
Only candidate Wine service executables from the supplied fixture runtime or
bundle are inspected. Raw command lines and environments remain in memory and
are never included in results, logs or exception messages.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import subprocess
import sys
import time


SERVICE = re.compile(r'(?:^|[\\/\s"])(wineserver|(?:services|winedevice|plugplay|svchost|rpcss|explorer|conhost)\.exe)(?=$|[\s"])', re.I)
IDENTITY = re.compile(r'(?:^|\s)((?:Steam|STEAM_)[A-Za-z0-9_]*|GLUON_HOST_PID|GLUON_GAME_CWD)=')
LOADER_NAMES = {'wine', 'wine64', 'wine-preloader', 'wine64-preloader'}


def _output(arguments, limit, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError('Retained CI Wine inspection exceeded its total deadline')
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, errors='replace', timeout=min(2, remaining))
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError('Could not obtain CI Wine service metadata') from None
    if result.returncode != 0 or len(result.stdout) > limit:
        raise RuntimeError('CI Wine service metadata was unavailable or exceeded its bound')
    return result.stdout


def _inside(path, directory):
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def inspect_retained_services(prefix, runtime, bundle, tracked_host_pid):
    """Assert a completed fixture left only detached services without game IDs.

    Call only after the tracked native launcher has been reaped and the caller
    has confirmed `wineserver --check-running == 0`. These metadata assertions
    do not model Steam's internal descendant cache or prove personal Steam state.
    """
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'darwin':
        raise RuntimeError('Retained-service inspection requires a GitHub Actions Mac')
    prefix, runtime, bundle = (Path(path).resolve(strict=True) for path in (prefix, runtime, bundle))
    tracked_host_pid = int(tracked_host_pid)
    if tracked_host_pid <= 1:
        raise ValueError('Expected a valid reaped CI launcher PID')
    deadline = time.monotonic() + 8
    libproc = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    libproc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    libproc.proc_pidpath.restype = ctypes.c_int

    def executable(pid):
        buffer = ctypes.create_string_buffer(4096)
        length = libproc.proc_pidpath(pid, buffer, len(buffer))
        if length <= 0:
            return None
        return Path(os.fsdecode(buffer.value)).resolve()

    if executable(tracked_host_pid) is not None:
        raise RuntimeError('Tracked CI launcher still exists after its wait completed')
    inventory = _output(['/bin/ps', '-axo', 'pid=,ppid=,command='], 4 * 1024 * 1024, deadline)
    candidates = []
    for line in inventory.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        role = SERVICE.search(fields[2])
        if role is None:
            continue
        pid = int(fields[0])
        image = executable(pid)
        selected_role = role.group(1).lower().removesuffix('.exe')
        expected_name = {'wineserver'} if selected_role == 'wineserver' else LOADER_NAMES
        if image is None or image.name not in expected_name:
            continue
        # The bundle is accepted for selection so an erroneous retained game
        # loader fails the assertions below, instead of disappearing from scope.
        if _inside(image, runtime) or _inside(image, bundle):
            candidates.append((pid, selected_role, image))
    if len(candidates) > 32:
        raise RuntimeError('Unexpected number of retained CI Wine service candidates')

    # Darwin ps does not delimit environment values with NUL. Match the entire
    # known fixture path, including any spaces, followed by the next KEY=value.
    exact_prefix = re.compile(r'(?:^|\s)WINEPREFIX=' + re.escape(str(prefix)) + r'(?=\s[A-Za-z_][A-Za-z0-9_]*=|$)')
    loader_paths = {str(runtime / 'bin/wine'), str((runtime / 'bin/wine').resolve())}
    normal_loader = re.compile(r'(?:^|\s)WINELOADER=(?:' + '|'.join(re.escape(path) for path in sorted(loader_paths)) +
                               r')(?=\s[A-Za-z_][A-Za-z0-9_]*=|$)')
    services = []
    for pid, role, before in candidates:
        try:
            environment = _output(['/bin/ps', 'eww', '-p', str(pid), '-o', 'command='], 1024 * 1024, deadline).strip()
        except RuntimeError:
            if executable(pid) is None:  # An ordinary service exited meanwhile.
                continue
            raise
        if exact_prefix.search(environment) is None:
            continue
        after = executable(pid)
        if after is None:
            continue
        if after != before:
            raise RuntimeError('A retained CI Wine service changed executable during inspection')
        parent = _output(['/bin/ps', '-p', str(pid), '-o', 'pid=,ppid='], 128, deadline).split()
        if len(parent) != 2 or not all(value.isdigit() for value in parent) or int(parent[0]) != pid:
            raise RuntimeError('Retained CI Wine service has no reliable PID metadata')
        parent = parent[1]
        if not parent.isdigit():
            raise RuntimeError('Retained CI Wine service has no reliable parent metadata')
        ppid = int(parent)
        has_identity = IDENTITY.search(environment) is not None
        has_game_loader = _inside(after, bundle) or str(bundle) in environment
        loader_matches = normal_loader.search(environment) is not None
        row = {'pid': pid, 'ppid': ppid, 'role': role, 'prefix_matches': True,
               'has_game_or_steam_identity': has_identity, 'has_game_bundle_loader': has_game_loader,
               'reparented_away_from_tracked_host': ppid != tracked_host_pid,
               'uses_fixture_runtime_loader': _inside(after, runtime),
               'loader_override_matches_runtime': loader_matches}
        services.append(row)
        # Never include the command/environment in this exception: it can contain
        # runner credentials unrelated to the generated fixture.
        if has_identity or has_game_loader or ppid == tracked_host_pid or not row['uses_fixture_runtime_loader'] or not loader_matches:
            raise RuntimeError('Retained CI Wine service failed identity/ownership checks (PID %d, role %s)' % (pid, role))
    if not any(row['role'] == 'wineserver' for row in services):
        raise RuntimeError('No retained native Wine server matched the exact CI prefix')
    if not any(row['role'] != 'wineserver' for row in services):
        raise RuntimeError('No retained system service matched the exact CI Wine prefix')
    if time.monotonic() >= deadline:
        raise RuntimeError('Retained CI Wine inspection exceeded its total deadline')
    return {'status': 'passed', 'tracked_host_exited': True,
            'retained_service_count': sum(row['role'] != 'wineserver' for row in services),
            'services': services,
            'scope': 'Prefix-scoped CI metadata after tracked-host exit; no raw environment retained',
            'limit': 'Does not inspect or prove real Steam descendant tracking'}
