#!/usr/bin/env python3
"""Statically check whether Wine's native hosts permit Steam's overlay loader.

Only codesign display commands run. No Wine executable or library is loaded.
"""

import argparse
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
from xml.parsers.expat import ExpatError


HOST_PATHS = (
    "bin/wine",
    "bin/wine64",
    "bin/wine-preloader",
    "bin/wine64-preloader",
    "lib/wine/x86_64-unix/wine",
    "lib/wine/i386-unix/wine",
)
CS_RUNTIME = 0x10000
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}
REQUIRED_ENTITLEMENTS = (
    "com.apple.security.cs.allow-dyld-environment-variables",
    "com.apple.security.cs.disable-library-validation",
)


class AuditError(Exception):
    pass


def native_hosts(runtime):
    """Keep inspection inside the resolved runtime, including loader symlinks."""
    try:
        root = Path(runtime).resolve(strict=True)
        if not root.is_dir():
            raise AuditError("runtime is not a directory")
        if not os.path.lexists(root / HOST_PATHS[0]):
            raise AuditError("bin/wine is missing")
        hosts = []
        seen = set()
        for relative in HOST_PATHS:
            candidate = root / relative
            if not os.path.lexists(candidate):
                continue
            target = candidate.resolve(strict=True)
            if not target.is_relative_to(root) or not target.is_file():
                raise AuditError(f"{relative}: loader must be a file inside the runtime")
            if target not in seen:
                with target.open("rb") as stream:
                    if stream.read(4) not in MACHO_MAGICS:
                        raise AuditError(f"{relative}: loader is not a native Mach-O file")
                seen.add(target)
                hosts.append(target)
        return root, hosts
    except (OSError, RuntimeError) as error:
        raise AuditError("cannot resolve the runtime's native loaders") from error


def display_signature(host, runner=subprocess.run):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("DYLD_")}
    environment.update(LC_ALL="C", LANG="C")

    def display(*options):
        try:
            result = runner(["/usr/bin/codesign", "-d", *options, str(host)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=10, check=False, env=environment)
        except (OSError, subprocess.SubprocessError) as error:
            raise AuditError("codesign inspection failed") from error
        if len(result.stdout) + len(result.stderr) > 1024 * 1024:
            raise AuditError("codesign inspection output is too large")
        return result

    details = display("--verbose=4")
    unsigned_message = os.fsencode(host) + b": code object is not signed at all"
    if (details.returncode == 1 and not details.stdout
            and details.stderr.strip() == unsigned_message):
        return False, {}
    if details.returncode != 0:
        raise AuditError("cannot inspect the code signature")
    flags = re.findall(rb"^CodeDirectory\b[^\r\n]*\bflags=0x([0-9a-fA-F]+)\b",
                       details.stderr, re.MULTILINE)
    if len(flags) != 1:
        raise AuditError("code signature flags are missing or ambiguous")

    entitlement_result = display("--entitlements", ":-")
    if entitlement_result.returncode != 0:
        raise AuditError("cannot inspect code signature entitlements")
    raw_entitlements = entitlement_result.stdout.strip()
    entitlements = {}
    if raw_entitlements:
        try:
            entitlements = plistlib.loads(raw_entitlements)
        except (ValueError, ExpatError) as error:
            raise AuditError("code signature entitlements are not a valid plist") from error
        if not isinstance(entitlements, dict):
            raise AuditError("code signature entitlements are not a dictionary")
    return bool(int(flags[0], 16) & CS_RUNTIME), entitlements


def audit_runtime(runtime, runner=subprocess.run):
    root, hosts = native_hosts(runtime)
    for host in hosts:
        relative = str(host.relative_to(root))
        try:
            hardened, entitlements = display_signature(host, runner)
            if hardened:
                missing = [name for name in REQUIRED_ENTITLEMENTS
                           if entitlements.get(name) is not True]
                if missing:
                    raise AuditError("hardened loader does not permit Steam overlay loading: "
                                     + ", ".join(missing))
        except AuditError as error:
            raise AuditError(f"{relative}: {error}") from error
    return len(hosts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", help="complete Wine runtime directory")
    args = parser.parse_args(argv)
    try:
        count = audit_runtime(args.runtime)
    except AuditError as error:
        print(f"Steam overlay policy check failed: {error}", file=sys.stderr)
        return 1
    print(f"Steam overlay loading policy passed for {count} native Wine hosts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
