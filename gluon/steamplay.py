"""Install NotProton-derived Steam Play hooks using staged, reversible bundles.

Adapted from NotProton's SteamInstaller/SteamBundle/Uninstall (GPL-3.0),
revision 8562a6320279224125a80cd66bf8d1af9b04bb2d. This does not patch appinfo.
Local recovery receipts contain hashes; none are distributed in Gluon's source.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import uuid

from . import runtime, steam, vdf

DYLIB = "gluon-steamplay.dylib"
INSERT = "DYLD_INSERT_LIBRARIES"
CONTROLLER = "SDL_JOYSTICK_BLACKLIST_DEVICES"
LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
VALVE_REQUIREMENT = 'anchor apple generic and certificate leaf[subject.OU] = "MXGJJ98X76"'


class SteamPlayError(RuntimeError):
    pass


def _run(*args):
    try:
        result = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        raise SteamPlayError("cannot run " + str(args[0])) from error
    if result.returncode:
        raise SteamPlayError("%s: %s" % (args[0], result.stderr.strip()))
    return result.stdout


def _json(path):
    if path.is_symlink():
        raise SteamPlayError("refusing symlink: " + str(path))
    return json.loads(path.read_text())


def _atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _write_json(path, data):
    _atomic(path, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode())


def _digest(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _tree(path):
    """Record contents, links and modes without following bundle symlinks."""
    result = {}
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            item = Path(root) / name
            meta = item.lstat()
            key = str(item.relative_to(path))
            if stat.S_ISLNK(meta.st_mode):
                value = ["link", os.readlink(item)]
            elif stat.S_ISREG(meta.st_mode):
                value = ["file", stat.S_IMODE(meta.st_mode), _digest(item)]
            elif stat.S_ISDIR(meta.st_mode):
                value = ["dir", stat.S_IMODE(meta.st_mode)]
            else:
                raise SteamPlayError("unsupported bundle entry: " + str(item))
            result[key] = value
    return result


def _plist(app):
    path = app / "Contents/Info.plist"
    if path.is_symlink() or not path.is_file():
        raise SteamPlayError("Steam Info.plist must be a regular file")
    try:
        value = plistlib.loads(path.read_bytes())
    except (ValueError, plistlib.InvalidFileException) as error:
        raise SteamPlayError("invalid Steam Info.plist") from error
    if (not isinstance(value, dict) or value.get("CFBundleIdentifier") != "com.valvesoftware.steam"
            or value.get("CFBundleExecutable") != "steam_osx"):
        raise SteamPlayError("this is not a Steam application bundle")
    environment = value.get("LSEnvironment", {})
    if not isinstance(environment, dict):
        raise SteamPlayError("invalid Steam LSEnvironment")
    return value


def _app_candidates():
    choices = [Path.home() / "Applications/Steam.app", Path("/Applications/Steam.app")]
    try:
        found = _run("/usr/bin/mdfind", "kMDItemCFBundleIdentifier == 'com.valvesoftware.steam'")
        choices += [Path(line) for line in found.splitlines() if line.endswith(".app")]
    except SteamPlayError:
        pass
    return choices


def find_app(explicit=None, *, recorded=(), excluded=()):
    """Reuse the registered app for upgrades; discover only on first install.

    Restoration bundles retain Steam's identifier and can appear in Spotlight.
    They must never compete with the registered app or become install targets.
    An explicit path still permits choosing another real Steam installation.
    """
    if explicit:
        choices = [Path(explicit).expanduser()]
    elif recorded:
        choices = [Path(name) for name in recorded]
        for app in choices:
            if not app.is_dir():
                raise SteamPlayError("tracked Steam app is missing; restore it before upgrading, or choose another installation with --steam-app PATH: " + str(app))
    else:
        choices = _app_candidates()
    excluded = [Path(path).resolve() for path in excluded]
    valid = []
    for app in choices:
        if not explicit and not recorded:
            resolved = app.resolve()
            if (any(resolved == path or path in resolved.parents for path in excluded)
                    or app.name.startswith((".gluon-old-", ".gluon-stage-"))
                    or resolved.name.startswith((".gluon-old-", ".gluon-stage-"))):
                continue
        if app.exists() and app.resolve() not in valid:
            _plist(app)
            valid.append(app.resolve())
    if not valid:
        raise SteamPlayError("Steam.app is missing; install and open Steam once, then run gluon install --steam-app PATH")
    if len(valid) != 1:
        raise SteamPlayError("multiple Steam apps found; select one with --steam-app PATH")
    return valid[0]


def client_build(root):
    versions = set()
    for base in (root, root / "Steam.AppBundle/Steam/Contents/MacOS"):
        for name in ("steam_client_macos.installed", "steam_client_osx.installed",
                     "steam_client_macos.manifest", "steam_client_osx.manifest",
                     "steam_client_signed_osx.manifest", "steam_client_signed-2_osx.manifest"):
            path = base / "package" / name
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix == ".manifest" and not path.with_suffix(".installed").is_file():
                continue
            try:
                data = vdf.load(str(path))
            except vdf.VDFError:
                continue
            for section in data.values():
                if isinstance(section, dict):
                    version = vdf.iget(section, "version")
                    if isinstance(version, str) and version.isdigit():
                        versions.add(version)
    if not versions:
        raise SteamPlayError("cannot identify the installed Steam build; open Steam to finish its update, then quit it")
    # Signed-channel manifests coexist with stale unsigned-channel manifests.
    # Only manifests with an installed inventory count; runtime resolution is
    # still mandatory and never trusts the version number alone.
    return max(versions, key=int)


class System:
    """Native operations are replaceable by fixtures; production never skips signing."""
    def copy(self, source, destination):
        _run("/usr/bin/ditto", source, destination)

    def sign(self, app):
        for path in (app / "Contents/MacOS" / DYLIB, app / "Contents/MacOS/steam_osx", app):
            _run("/usr/bin/codesign", "--force", "--sign", "-", path)
        self.verify(app)

    def verify(self, app):
        _run("/usr/bin/codesign", "--verify", "--strict", app)

    def valve(self, app):
        self.verify(app)
        _run("/usr/bin/codesign", "--verify", "--test-requirement",
             "=" + VALVE_REQUIREMENT, app)

    def register(self, app):
        _run(LSREGISTER, "-f", app)


class Installation:
    def __init__(self, home=None, root=None, system=None):
        self.home = Path(home or steam.GLUON_HOME).resolve()
        self.root = Path(root or steam.STEAM_ROOT).resolve()
        self.state = self.home / "state/steamplay.json"
        self.payload = self.home / "steamplay/current"
        pending = self.home / "steamplay/pending.json"
        if pending.exists():
            name = _json(pending).get("name")
            if not isinstance(name, str) or not name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_" for c in name) or name in (".", ".."):
                raise SteamPlayError("invalid packaged Steam Play selection")
            self.payload = self.home / "steamplay/payloads" / name
        self.system = system or System()

    @contextmanager
    def lock(self, name="steamplay.lock"):
        self.state.parent.mkdir(parents=True, exist_ok=True)
        path = self.state.parent / name
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise SteamPlayError("another Gluon installation is running") from error
            yield
        finally:
            os.close(fd)

    def idle(self):
        if not runtime._idle(home=self.home):
            raise SteamPlayError("quit Steam, Steam helpers and Wine games before changing Steam Play")
        result = subprocess.run(["pgrep", "-x", "steamwebhelper|Steam Helper"], capture_output=True)
        if result.returncode != 1:
            raise SteamPlayError("Steam helpers are still running; quit Steam completely")

    @contextmanager
    def bundle_lock(self, app):
        path = app.parent / ("." + app.name + ".gluon.lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(fd).st_uid != os.getuid():
                raise SteamPlayError("Steam installation lock belongs to another account")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise SteamPlayError("another installer is modifying this Steam app") from error
            yield
        finally:
            os.close(fd)

    def receipt(self):
        if not self.state.exists():
            return {"schema": 1, "apps": {}}
        value = _json(self.state)
        if value.get("schema") != 1 or not isinstance(value.get("apps"), dict):
            raise SteamPlayError("invalid Steam Play recovery receipt; backups retained")
        return value

    def payload_check(self):
        manifest = _json(self.payload / "payload.json")
        if manifest.get("schema") != 1 or not manifest.get("files") or not manifest.get("builds"):
            raise SteamPlayError("incomplete Steam Play payload")
        for name, digest in manifest["files"].items():
            path = self.payload / name
            if (Path(name).is_absolute() or ".." in Path(name).parts or path.is_symlink()
                    or not path.is_file() or _digest(path) != digest):
                raise SteamPlayError("Steam Play payload failed verification: " + name)
        for name in (DYLIB, "appinfo", "tools", "builds", "run", "valve-packages.manifest"):
            if name not in manifest["files"]:
                raise SteamPlayError("missing Steam Play payload file: " + name)
        build = client_build(self.root)
        if build not in manifest["builds"]:
            raise SteamPlayError("unsupported Steam build %s; no Steam changes made. Install an updated Gluon payload" % build)
        return manifest, build

    def _writable(self, app):
        for path in (app, app.parent, app / "Contents", app / "Contents/MacOS", app / "Contents/Info.plist"):
            owners = (os.getuid(), 0) if path == app.parent else (os.getuid(),)
            if path.is_symlink() or path.stat().st_uid not in owners or not os.access(path, os.W_OK):
                raise SteamPlayError("this account must own Steam and be able to write Steam and its parent: " + str(path))

    def _foreign(self, app, receipt):
        env = _plist(app).get("LSEnvironment", {})
        insert = env.get(INSERT, "")
        expected = str(app / "Contents/MacOS" / DYLIB)
        if not isinstance(insert, str) or (insert and (insert != expected or receipt is None)):
            raise SteamPlayError("foreign or untracked Steam injection; restore it with its owner before installing Gluon")
        if os.path.lexists(app / "Contents/MacOS" / DYLIB) and receipt is None:
            raise SteamPlayError("untracked Steam Play library; recovery receipt required")

    def _tool_check(self):
        for name in ("gluon", "gluon-proton"):
            tool = self.root / "compatibilitytools.d" / name
            if not os.path.lexists(tool):
                continue
            marker = tool / "run"
            if tool.is_symlink() or not tool.is_dir() or marker.is_symlink() or not marker.is_file() or b"# gluon Steam Play compatibility tool shim\n" not in marker.read_bytes()[:128]:
                raise SteamPlayError("a foreign compatibility tool occupies " + str(tool))

    def _owned_drift(self, app, current, record):
        """Repair missing/changed owned injection files without accepting foreign edits."""
        owned = {"Contents/Info.plist", "Contents/MacOS/" + DYLIB}
        expected = record["installed"]
        if {k: v for k, v in current.items() if k not in owned} != {k: v for k, v in expected.items() if k not in owned}:
            return False
        if "plist" not in record:
            return False
        actual = _plist(app)
        wanted = plistlib.loads(plistlib.dumps(record["plist"]))
        original = _plist(Path(record["backup"]))
        actual_env = actual.setdefault("LSEnvironment", {})
        wanted_env = wanted.setdefault("LSEnvironment", {})
        original_env = original.get("LSEnvironment", {})
        for key in (INSERT, CONTROLLER, "GLUON_HOME", "GLUON_STEAM_ROOT", "GLUON_STEAMPLAY_PAYLOAD"):
            if key in actual_env and actual_env[key] not in (wanted_env.get(key), original_env.get(key)):
                return False
            actual_env.pop(key, None)
            wanted_env.pop(key, None)
        return actual == wanted

    def _recover(self, data):
        """A crash after the first rename must not strand Steam at a hidden path."""
        transaction = data.get("transaction")
        if not transaction:
            return
        app, old, stage = (Path(transaction[k]) for k in ("app", "old", "stage"))
        if old.parent != app.parent or stage.parent != app.parent or not old.name.startswith(".gluon-old-") or not stage.name.startswith(".gluon-stage-"):
            raise SteamPlayError("invalid Steam Play transaction; manual recovery needed")
        with self.bundle_lock(app):
            if old.exists():
                if app.exists():
                    if _tree(app) != transaction["new_tree"]:
                        raise SteamPlayError("interrupted installation changed; recovery copies retained at " + str(old))
                    shutil.rmtree(app)
                os.rename(old, app)
            if stage.exists():
                shutil.rmtree(stage)
        data.pop("transaction")
        _write_json(self.state, data)

    def _publish(self, app, stage, data, record):
        old = app.parent / (".gluon-old-" + uuid.uuid4().hex + ".app")
        new_tree = _tree(stage)
        data["transaction"] = {"app": str(app), "old": str(old), "stage": str(stage), "new_tree": new_tree}
        _write_json(self.state, data)
        self.idle()
        os.rename(app, old)
        try:
            os.rename(stage, app)
            self.system.verify(app)
            self.system.register(app)
            if record is None:
                data["apps"].pop(str(app), None)
            else:
                data["apps"][str(app)] = record
            data.pop("transaction")
            _write_json(self.state, data)
        except BaseException:
            if app.exists():
                shutil.rmtree(app)
            os.rename(old, app)
            self.system.register(app)
            raise
        else:
            shutil.rmtree(old)

    def install(self, explicit=None):
        with self.lock(), ExitStack() as locks:
            self.idle()
            data = self.receipt()
            self._recover(data)
            app = find_app(explicit, recorded=data["apps"], excluded=(self.home / "backups",))
            self._writable(app)
            locks.enter_context(self.bundle_lock(app))
            manifest, build = self.payload_check()
            previous = data["apps"].get(str(app))
            self._foreign(app, previous)
            self._tool_check()
            before = _tree(app)
            if previous and before == previous["installed"] and previous["payload"] == manifest:
                self.system.verify(app)
                self.system.register(app)
                return ["Steam Play is current at " + str(app)]
            if previous and before != previous["installed"] and not self._owned_drift(app, before, previous):
                # Only a newly Valve-signed bundle may replace the restoration baseline.
                # Arbitrary foreign changes must never be overwritten during repair.
                self.system.valve(app)
                if _plist(app).get("LSEnvironment", {}).get(INSERT):
                    raise SteamPlayError("Steam changed since installation; restore the preserved bundle before reinstalling")
                previous = None
            if previous is None:
                self.system.valve(app)
                backup = self.home / "backups/steamplay" / uuid.uuid4().hex / "Steam.app"
                backup.parent.mkdir(parents=True)
                self.system.copy(app, backup)
                if _tree(backup) != before:
                    raise SteamPlayError("Steam backup verification failed")
                original = {"backup": str(backup), "original": before}
            else:
                original = {k: previous[k] for k in ("backup", "original")}
            stage = app.parent / (".gluon-stage-" + uuid.uuid4().hex + ".app")
            try:
                self.system.copy(app, stage)
                shutil.copy2(self.payload / DYLIB, stage / "Contents/MacOS" / DYLIB)
                plist = _plist(stage)
                env = plist.setdefault("LSEnvironment", {})
                env.update({INSERT: str(app / "Contents/MacOS" / DYLIB),
                            CONTROLLER: "0x05ac/0x0004", "GLUON_HOME": str(self.home),
                            "GLUON_STEAM_ROOT": str(self.root),
                            "GLUON_STEAMPLAY_PAYLOAD": str(self.payload)})
                _atomic(stage / "Contents/Info.plist", plistlib.dumps(plist))
                self.system.sign(stage)
                record = dict(original, installed=_tree(stage), payload=manifest, build=build, plist=plist)
                self.idle()
                if _tree(app) != before:
                    raise SteamPlayError("Steam changed during staging; installation cancelled")
                # Migration is reversible and runs only after staging/signing succeeds.
                from . import migration
                notes = migration.migrate()
                self._publish(app, stage, data, record)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
            return notes + ["Installed Steam Play at %s (build %s); restart Steam" % (app, build)]

    def uninstall(self):
        with self.lock(), ExitStack() as locks:
            self.idle()
            data = self.receipt()
            self._recover(data)
            notes = []
            for name, record in sorted(list(data["apps"].items())):
                app = Path(name)
                if not app.exists():
                    raise SteamPlayError("tracked Steam app is missing; reconnect it before uninstalling: " + name)
                self._writable(app)
                locks.enter_context(self.bundle_lock(app))
                self._foreign(app, record)
                current = _tree(app)
                if current != record["installed"] and not self._owned_drift(app, current, record):
                    # A clean Steam update already removed our changes.
                    self.system.valve(app)
                    if _plist(app).get("LSEnvironment", {}).get(INSERT) or (app / "Contents/MacOS" / DYLIB).exists():
                        raise SteamPlayError("Steam changed; refusing to overwrite it. Recovery copy: " + record["backup"])
                    del data["apps"][name]
                    _write_json(self.state, data)
                    continue
                backup = Path(record["backup"])
                if _tree(backup) != record["original"]:
                    raise SteamPlayError("Steam restoration backup is missing or changed: " + str(backup))
                self.system.valve(backup)
                stage = app.parent / (".gluon-stage-" + uuid.uuid4().hex + ".app")
                try:
                    self.system.copy(backup, stage)
                    self._publish(app, stage, data, None)
                finally:
                    if stage.exists():
                        shutil.rmtree(stage)
                notes.append("Restored original signed Steam bundle at " + name)
            for tool_name in ("gluon", "gluon-proton"):
                tools = self.root / "compatibilitytools.d" / tool_name
                marker = tools / "run"
                if not tools.is_symlink() and not marker.is_symlink() and marker.is_file() and b"# gluon Steam Play compatibility tool shim\n" in marker.read_bytes()[:128]:
                    for name in ("run", "toolmanifest.vdf", "compatibilitytool.vdf", "flavor", "build"):
                        path = tools / name
                        if path.is_file() or path.is_symlink():
                            path.unlink()
                    if not any(tools.iterdir()):
                        tools.rmdir()
            return notes

    def status(self):
        data = self.receipt()
        if data.get("transaction"):
            return ["Interrupted installation; run gluon repair"]
        lines = []
        for name, record in data["apps"].items():
            app = Path(name)
            current = app.is_dir() and _tree(app) == record["installed"]
            try:
                manifest, build = self.payload_check()
                current = current and record["payload"] == manifest and record["build"] == build
            except (SteamPlayError, OSError, ValueError):
                current = False
            lines.append("%s: %s" % (name, "installed; hook activation must be checked in steamplay.log" if current else "outdated or unsupported; quit Steam and run gluon repair"))
        return lines or ["Steam Play is not installed"]
