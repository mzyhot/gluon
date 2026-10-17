"""Development command line:  python3 -m gluon <command>"""
from __future__ import annotations

import argparse
import os
import sys
import time

from . import cef, enable, runtime, steam, steam_compat

# After Steam quits, wait this long before editing its files, so a client that relaunches
# itself (after an update, say) is seen as running again.
QUIT_SETTLE = 2.0


TOOL_COPY = {
    "absent": "not registered",
    "shim": "shim to the installed launcher",
    "differs": "STALE - replace the old tool copy with the installed-launcher shim",
    "unusable": "NOT executable - reinstall the tool shim",
    "unavailable": "shim target unavailable - install the Gluon launcher",
    "unknown": "cannot verify - canonical tool shim is missing",
}


def cmd_status(_args: argparse.Namespace) -> int:
    print("Steam root   :", steam.STEAM_ROOT)
    print("Steam running:", "yes" if steam.is_running() else "no")
    installed = os.access(steam.INSTALLED_LAUNCHER, os.X_OK)
    print("Launcher     :", steam.INSTALLED_LAUNCHER if installed else "NOT installed (%s)" % steam.INSTALLED_LAUNCHER)
    print("Steam tool   :", "%s (%s)" % (TOOL_COPY[steam.tool_copy_state()], steam.TOOL_DIR))
    runtime = os.path.join(steam.GLUON_HOME, "runtime", "current")
    if os.access(os.path.join(runtime, "proton"), os.X_OK):
        kind = "Steam Play tool"
    elif os.access(os.path.join(runtime, "bin", "wine"), os.X_OK):
        kind = "plain Wine"
    else:
        kind = None
    print("Runtime      :", "%s (%s)" % (os.path.realpath(runtime), kind) if kind else "NOT found (%s)" % runtime)
    rows = enable.status()
    print("Enabled apps :", len(rows))
    for r in rows:
        state = "in effect" if r["in_effect"] else "REFRESHED BY STEAM - restart Steam so Gluon can restore compatibility"
        print("  %-8d %-32s %s (change %s)" % (r["appid"], r["name"], state, r["cache_change"]))
    return 0


def cmd_enable(args: argparse.Namespace) -> int:
    # Launch options always name the installed launcher, never Steam's tool shim.
    if not os.access(steam.INSTALLED_LAUNCHER, os.X_OK):
        raise enable.EnableError("Gluon's launcher is not installed (%s); install the Gluon package first"
                                 % steam.INSTALLED_LAUNCHER)
    for line in enable.enable(args.appid):
        print(line)
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    appids = [int(k) for k in enable.load_state()] if args.all else args.appid
    for line in enable.disable(appids):
        print(line)
    return 0


def _report(args: argparse.Namespace, lines: list) -> None:
    if args.log and lines:
        path = os.path.join(steam.GLUON_HOME, "logs", "agent.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            for line in lines:
                f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    else:
        for line in lines:
            print(line)


def cmd_sync(args: argparse.Namespace) -> int:
    started = time.monotonic()
    # While Steam runs, stay up until it quits (within the budget) and sync again then,
    # so a quick relaunch finds Gluon's details back in place. Checked before the first
    # pass so a quit during that pass is not missed.
    waiting = args.wait_for_quit > 0 and not args.dry_run and steam.is_running()
    _report(args, runtime.activate_pending(dry_run=args.dry_run) + enable.sync(dry_run=args.dry_run))
    if waiting:
        remaining = args.wait_for_quit - (time.monotonic() - started)
        if remaining > 0 and steam.wait_for_exit(remaining):
            time.sleep(QUIT_SETTLE)
            _report(args, runtime.activate_pending() + enable.sync())
    return 0


def cmd_candidates(_args: argparse.Namespace) -> int:
    for appid, name in enable.candidates():
        print("%-8d %s" % (appid, name))
    return 0


def cmd_console(args: argparse.Namespace) -> int:
    with cef.SteamBridge() as bridge:
        print(bridge.console(" ".join(args.command), wait=args.wait))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gluon")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show installation state and enabled apps").set_defaults(fn=cmd_status)
    s = sub.add_parser("steam-probe", help="preview an explicit read-only Steam compatibility probe (no hooks)")
    s.add_argument("--library", help="probe dylib downloaded from GitHub Actions")
    s.add_argument("--launch", action="store_true", help="start Steam with the probe; requires Steam closed")
    s.set_defaults(fn=steam_compat.cmd_probe)
    s = sub.add_parser("enable", help="make Windows games installable, including 32-bit-only Mac games (Steam must be closed)")
    s.add_argument("appid", type=int, nargs="+")
    s.set_defaults(fn=cmd_enable)
    s = sub.add_parser("disable", help="restore the original metadata for apps (Steam must be closed)")
    s.add_argument("appid", type=int, nargs="*")
    s.add_argument("--all", action="store_true", help="every app Gluon enabled")
    s.set_defaults(fn=cmd_disable)
    s = sub.add_parser("sync", help="restore and protect managed games while Steam is closed; warn about refreshed metadata while it runs")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--log", action="store_true", help="append results to logs/agent.log instead of printing")
    s.add_argument("--wait-for-quit", type=float, default=0, metavar="SECONDS",
                   help="if Steam is running, wait until SECONDS after start for it to quit, then sync again")
    s.set_defaults(fn=cmd_sync)
    sub.add_parser("candidates", help="list cached games with a Windows build and no Mac build current macOS runs").set_defaults(fn=cmd_candidates)
    s = sub.add_parser("console", help="run a Steam console command (needs Steam started with -cef-enable-debugging)")
    s.add_argument("command", nargs="+")
    s.add_argument("--wait", type=float, default=1.5)
    s.set_defaults(fn=cmd_console)
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.fn(args)
    except (enable.EnableError, cef.BridgeError, runtime.RuntimeSelectionError, steam.SteamError, OSError) as e:
        print("gluon: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
