"""Signature fixtures verify the packaging gate without executing a Wine host."""

import importlib.util
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "packaging/check-overlay-policy.py"
SPEC = importlib.util.spec_from_file_location("gluon_overlay_policy", SCRIPT)
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


class OverlayPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gluon-signature-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "Runtime"
        self.host = self.root / "bin/wine"
        self.host.parent.mkdir(parents=True)
        self.host.write_bytes(b"\xcf\xfa\xed\xfe" + b"signature fixture; never executed")
        self.calls = []

    def inspector(self, flags="0x0(none)", entitlements=None, unsigned=False,
                  details_error=False, entitlement_error=False, raw_plist=None,
                  raw_details=None):
        def run(command, **kwargs):
            self.calls.append(command)
            host = command[-1]
            if "--verbose=4" in command:
                if unsigned:
                    return subprocess.CompletedProcess(command, 1, b"",
                                                       (host + ": code object is not signed at all\n").encode())
                if details_error:
                    return subprocess.CompletedProcess(command, 1, b"", b"permission denied\n")
                output = raw_details if raw_details is not None else (
                    f"CodeDirectory v=20500 size=793 flags={flags} hashes=8\n"
                    "Executable Segment flags=0x5\n").encode()
                return subprocess.CompletedProcess(command, 0, b"", output)
            if entitlement_error:
                return subprocess.CompletedProcess(command, 1, b"", b"cannot read entitlements\n")
            output = raw_plist if raw_plist is not None else (
                plistlib.dumps(entitlements) if entitlements is not None else b"")
            return subprocess.CompletedProcess(command, 0, output, b"display only\n")
        return run

    def test_observed_hardened_runtime_without_dyld_exception_is_rejected(self):
        # The development Wine host had these JIT/loader exceptions, but still
        # silently ignored DYLD_INSERT_LIBRARIES because the dyld exception was absent.
        entitlements = {
            "com.apple.security.cs.allow-unsigned-executable-memory": True,
            "com.apple.security.cs.disable-executable-page-protection": True,
            "com.apple.security.cs.disable-library-validation": True,
        }
        with self.assertRaisesRegex(policy.AuditError, "allow-dyld-environment-variables"):
            policy.audit_runtime(self.root, self.inspector("0x10000(runtime)", entitlements))

    def test_hardened_runtime_requires_both_boolean_exceptions(self):
        for missing in policy.REQUIRED_ENTITLEMENTS:
            for wrong_value in (False, 1, "true", None):
                with self.subTest(missing=missing, wrong_value=wrong_value):
                    entitlements = dict.fromkeys(policy.REQUIRED_ENTITLEMENTS, True)
                    if wrong_value is None:
                        del entitlements[missing]
                    else:
                        entitlements[missing] = wrong_value
                    with self.assertRaises(policy.AuditError):
                        policy.audit_runtime(self.root, self.inspector("0x10000(runtime)", entitlements))
        self.assertEqual(policy.audit_runtime(
            self.root, self.inspector("0x10000(runtime)",
                                      dict.fromkeys(policy.REQUIRED_ENTITLEMENTS, True))), 1)

    def test_unsigned_and_unhardened_hosts_are_accepted(self):
        for runner in (self.inspector(unsigned=True), self.inspector("0x2(adhoc)"), self.inspector()):
            self.assertEqual(policy.audit_runtime(self.root, runner), 1)

    def test_unknown_inspection_failures_are_rejected(self):
        for runner in (self.inspector(details_error=True), self.inspector(entitlement_error=True)):
            with self.assertRaises(policy.AuditError):
                policy.audit_runtime(self.root, runner)

        def timeout(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 10)
        with self.assertRaisesRegex(policy.AuditError, "inspection failed"):
            policy.audit_runtime(self.root, timeout)

    def test_malformed_entitlements_are_rejected_even_on_unhardened_hosts(self):
        for output in (b"not a plist", b'<?xml version="1.0"?><plist><dict><key>x</key>',
                       plistlib.dumps(["not a dictionary"])):
            with self.subTest(output=output):
                with self.assertRaises(policy.AuditError):
                    policy.audit_runtime(self.root, self.inspector(raw_plist=output))

    def test_missing_and_ambiguous_code_directory_flags_are_rejected(self):
        line = b"CodeDirectory v=20500 size=793 flags=0x2(adhoc) hashes=8\n"
        for output in (b"Executable Segment flags=0x5\n", line + line):
            with self.subTest(output=output):
                with self.assertRaisesRegex(policy.AuditError, "flags are missing or ambiguous"):
                    policy.audit_runtime(self.root, self.inspector(raw_details=output))

    def test_unsigned_shell_script_is_not_a_native_wine_host(self):
        self.host.write_bytes(b"#!/bin/sh\nexit 0\n")
        with self.assertRaisesRegex(policy.AuditError, "not a native Mach-O"):
            policy.audit_runtime(self.root, self.inspector(unsigned=True))
        self.assertEqual(self.calls, [])

    def test_internal_loader_links_are_deduplicated_but_child_host_is_checked(self):
        self.host.rename(self.root / "bin/wineloader")
        self.host.symlink_to("wineloader")
        (self.root / "bin/wine64").symlink_to("wineloader")
        child = self.root / "lib/wine/x86_64-unix/wine"
        child.parent.mkdir(parents=True)
        child.write_bytes(b"\xcf\xfa\xed\xfe" + b"child host signature fixture; never executed")
        self.assertEqual(policy.audit_runtime(self.root, self.inspector()), 2)
        inspected = {Path(command[-1]) for command in self.calls}
        self.assertEqual(inspected, {self.root / "bin/wineloader", child})

    def test_missing_dangling_and_outside_loaders_fail_before_inspection(self):
        self.host.unlink()
        with self.assertRaises(policy.AuditError):
            policy.audit_runtime(self.root, self.inspector())
        self.host.symlink_to("missing-loader")
        with self.assertRaises(policy.AuditError):
            policy.audit_runtime(self.root, self.inspector())
        self.host.unlink()
        outside = self.root.parent / "outside-loader"
        outside.write_bytes(b"outside fixture; never executed")
        self.host.symlink_to(outside)
        with self.assertRaises(policy.AuditError):
            policy.audit_runtime(self.root, self.inspector())
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
