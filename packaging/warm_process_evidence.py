"""CI-only evidence for detached, identity-free retained Wine services.

This module never discovers or inspects processes outside a GitHub Actions Mac.
Only candidate service images identified by the fixture runtime's loader inode
or generated bundle are inspected. Raw command lines and environments remain in memory and
are never included in results, logs or exception messages.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import subprocess
import sys
import struct
import time


# CodeWeavers' no-preloader macOS path gives a native loader hardlink the
# Windows program's name, sometimes without its .exe suffix. Names select
# candidates only; exact loader inode and fixture-prefix checks grant authority.
SERVICE = re.compile(r'(?:^|[\\/\s"])(wineserver|(?:services|winedevice|plugplay|svchost|rpcss|explorer|conhost)(?:\.exe)?)(?=$|[\s"])', re.I)


class RetainedServiceError(RuntimeError):
    """A failed assertion with bounded, already-sanitized fixture evidence."""
    def __init__(self, message, evidence):
        super().__init__(message)
        self.evidence = evidence


def parse_process_environment(data):
    """Decode Darwin KERN_PROCARGS2 without flattening argv/environment values."""
    if len(data) < 5 or len(data) > 1024 * 1024 or not data.endswith(b'\0'):
        raise RuntimeError('Invalid CI process argument buffer')
    argc = struct.unpack_from('=i', data)[0]
    if not 1 <= argc <= 65536:
        raise RuntimeError('Invalid CI process argument count')
    position = data.find(b'\0', 4)
    if position < 0:
        raise RuntimeError('Incomplete CI executable path')
    while position < len(data) and data[position] == 0:
        position += 1
    for _ in range(argc):
        position = data.find(b'\0', position)
        if position < 0:
            raise RuntimeError('Incomplete CI argument vector')
        position += 1
    result = {}
    for entry in data[position:].split(b'\0'):
        if not entry:
            continue
        key, equal, value = entry.partition(b'=')
        if not equal:
            raise RuntimeError('Incomplete CI environment record')
        key, value = os.fsdecode(key), os.fsdecode(value)
        if key in result and result[key] != value:
            raise RuntimeError('Ambiguous CI environment record')
        result[key] = value
    return result


def _process_environment(pid):
    # ps renders a title and environment into one ambiguous string; Wine changes
    # its process title. Read the kernel's NUL-delimited argument block instead.
    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
    buffer = ctypes.create_string_buffer(1024 * 1024)
    size = ctypes.c_size_t(len(buffer))
    libc.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint,
                           ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
    libc.sysctl.restype = ctypes.c_int
    if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0):
        raise RuntimeError('Could not obtain CI process environment')
    return parse_process_environment(buffer.raw[:size.value])


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
    diagnostics = {'status': 'inspecting', 'role_candidates': 0, 'loader_candidates': 0,
                   'unresolved_native_images': 0, 'different_loader_images': 0,
                   'prefix_mismatches': 0, 'exited_during_inspection': 0, 'services': []}

    def fail(message):
        diagnostics['status'] = 'failed'
        raise RetainedServiceError(message, diagnostics)

    loader_images = [path for path in (runtime / 'bin/wine', runtime / 'bin/wine64',
        runtime / 'bin/wine-preloader', runtime / 'bin/wine64-preloader',
        runtime / 'lib/wine/x86_64-unix/wine') if path.is_file()]
    server_image = runtime / 'bin/wineserver'

    def same_image(image, expected):
        try:
            return any(os.path.samefile(image, path) for path in expected)
        except OSError:
            return False
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
        diagnostics['role_candidates'] += 1
        pid = int(fields[0])
        image = executable(pid)
        selected_role = role.group(1).lower().removesuffix('.exe')
        if image is None:
            diagnostics['unresolved_native_images'] += 1
            continue
        expected = [server_image] if selected_role == 'wineserver' else loader_images
        # Native loader hardlinks can reside under /tmp/winetemp-.../<program>.
        # Path/name acceptance would miss them or trust an unrelated executable.
        # The generated game bundle stays in scope so its retained services fail.
        if same_image(image, expected) or _inside(image, bundle):
            candidates.append((pid, selected_role, image))
            diagnostics['loader_candidates'] += 1
        else:
            diagnostics['different_loader_images'] += 1
    if len(candidates) > 32:
        fail('Unexpected number of retained CI Wine service candidates')

    # Accept only an inode-identical prefix and a known compiled runtime loader.
    loader_paths = {str(path) for image in loader_images for path in (image, image.resolve())}
    diagnostics['rejected_prefix_candidates'] = []
    services = []
    for pid, role, before in candidates:
        try:
            environment = _process_environment(pid)
        except RuntimeError:
            if executable(pid) is None:  # An ordinary service exited meanwhile.
                diagnostics['exited_during_inspection'] += 1
                continue
            raise
        candidate_prefix = environment.get('WINEPREFIX', '')
        try:
            prefix_matches = Path(candidate_prefix).is_absolute() and os.path.samefile(candidate_prefix, prefix)
        except OSError:
            prefix_matches = False
        if not prefix_matches:
            diagnostics['prefix_mismatches'] += 1
            diagnostics['rejected_prefix_candidates'].append({
                'pid': pid, 'role': role, 'has_prefix_key': 'WINEPREFIX' in environment,
                'has_configdir_key': 'WINECONFIGDIR' in environment,
                'prefix_is_absolute': Path(candidate_prefix).is_absolute(),
                'prefix_mentions_fixture': str(prefix) in candidate_prefix,
                'has_loader_key': 'WINELOADER' in environment})
            continue
        after = executable(pid)
        if after is None:
            diagnostics['exited_during_inspection'] += 1
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
        has_identity = any(key.startswith(('Steam', 'STEAM_')) or key in ('GLUON_HOST_PID', 'GLUON_GAME_CWD') for key in environment)
        has_game_loader = _inside(after, bundle) or any(str(bundle) in value for value in environment.values())
        loader_matches = environment.get('WINELOADER') in loader_paths
        expected = [server_image] if role == 'wineserver' else loader_images
        image_matches = same_image(after, expected)
        row = {'pid': pid, 'ppid': ppid, 'role': role, 'prefix_matches': True,
               'has_game_or_steam_identity': has_identity, 'has_game_bundle_loader': has_game_loader,
               'reparented_away_from_tracked_host': ppid != tracked_host_pid,
               'uses_fixture_runtime_loader': image_matches,
               'runtime_loader_alias': image_matches and not _inside(after, runtime),
               'loader_override_matches_runtime': loader_matches}
        services.append(row)
        diagnostics['services'] = services
        # Never include the command/environment in this exception: it can contain
        # runner credentials unrelated to the generated fixture.
        if has_identity or has_game_loader or ppid == tracked_host_pid or not row['uses_fixture_runtime_loader'] or not loader_matches:
            fail('Retained CI Wine service failed identity/ownership checks (PID %d, role %s)' % (pid, role))
    if not any(row['role'] == 'wineserver' for row in services):
        fail('No retained native Wine server matched the exact CI prefix')
    if not any(row['role'] != 'wineserver' for row in services):
        fail('No retained system service matched the exact CI Wine prefix')
    if time.monotonic() >= deadline:
        raise RuntimeError('Retained CI Wine inspection exceeded its total deadline')
    return {'status': 'passed', 'tracked_host_exited': True,
            'retained_service_count': sum(row['role'] != 'wineserver' for row in services),
            'services': services,
            'candidate_counts': {key: value for key, value in diagnostics.items() if key not in ('services', 'status')},
            'scope': 'Prefix-scoped CI metadata after tracked-host exit; no raw environment retained',
            'limit': 'Does not inspect or prove real Steam descendant tracking'}
