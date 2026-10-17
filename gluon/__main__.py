"""Development command line:  python3 -m gluon <command>"""
from __future__ import annotations

import argparse
import os
import sys

from . import cef, enable, runtime, steam


def cmd_status(_args: argparse.Namespace) -> int:
    print("Steam root   :", steam.STEAM_ROOT)
    print("Steam running:", "yes" if steam.is_running() else "no")
    print("Gluon tool   :", "installed" if os.access(steam.LAUNCHER, os.X_OK) else "NOT installed", "(%s)" % steam.TOOL_DIR)
    runtime = os.path.join(steam.GLUON_HOME, "runtime", "current")
    if os.access(os.path.join(runtime, "proton"), os.X_OK):
        kind = "Steam Play tool"
    elif os.access(os.path.join(runtime, "bin", "wine"), os.X_OK):
        kind = "plain Wine"
    else:
        kind = None
    print("Runtime      :", "%s (%s)" % (os.path.realpath(runtime), kind) if kind else "NOT found (%s)" % runtime)
    print("Launcher     :", steam.LAUNCHER)
    rows = enable.status()
    print("Enabled apps :", len(rows))
    for r in rows:
        state = "in effect" if r["in_effect"] else "REFRESHED BY STEAM - restart Steam so Gluon can restore compatibility"
        print("  %-8d %-32s %s (change %s)" % (r["appid"], r["name"], state, r["cache_change"]))
    return 0


def cmd_enable(args: argparse.Namespace) -> int:
    for line in enable.enable(args.appid):
        print(line)
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    appids = [int(k) for k in enable.load_state()] if args.all else args.appid
    for line in enable.disable(appids):
        print(line)
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    lines = runtime.activate_pending(dry_run=args.dry_run)
    lines += enable.sync(dry_run=args.dry_run)
    if args.log and lines:
        import time
        path = os.path.join(steam.GLUON_HOME, "logs", "agent.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            for line in lines:
                f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    else:
        for line in lines:
            print(line)
    return 0


def cmd_candidates(_args: argparse.Namespace) -> int:
    for appid, name in enable.candidates():
        print("%-8d %s" % (appid, name))
    return 0


def cmd_console(args: argparse.Namespace) -> int:
    with cef.SteamBridge() as bridge:
        print(bridge.console(" ".join(args.command), wait=args.wait))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="gluon")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show installation state and enabled apps").set_defaults(fn=cmd_status)
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
    s.set_defaults(fn=cmd_sync)
    sub.add_parser("candidates", help="list cached games with a Windows build and no Mac build current macOS runs").set_defaults(fn=cmd_candidates)
    s = sub.add_parser("console", help="run a Steam console command (needs Steam started with -cef-enable-debugging)")
    s.add_argument("command", nargs="+")
    s.add_argument("--wait", type=float, default=1.5)
    s.set_defaults(fn=cmd_console)
    args = p.parse_args()
    try:
        return args.fn(args)
    except (enable.EnableError, cef.BridgeError, runtime.RuntimeSelectionError, steam.SteamError) as e:
        print("gluon: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
