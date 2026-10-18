"""Gluon installation and runtime maintenance commands."""
from __future__ import annotations

import argparse
import os
import sys
import time

from . import cef, cloud, migration, runtime, steam, steamplay


def main():
    parser = argparse.ArgumentParser(prog="gluon")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show Steam Play deployment and runtime state")
    for name in ("install", "repair"):
        command = sub.add_parser(name, help="install or repair native Steam Play while Steam and Wine are closed")
        command.add_argument("--steam-app", help="path to the Steam application to configure")
    sub.add_parser("uninstall", help="restore all Steam apps managed by Gluon; preserve saves and backups")
    sub.add_parser("migrate", help="restore legacy Gluon metadata once; keep originals and backups")
    command = sub.add_parser("cloud", help="inspect or repair automatic native Steam Cloud preparation")
    command.add_argument("operation", choices=("prepare", "status"))
    command.add_argument("appid")
    command = sub.add_parser("sync", help="select complete runtime updates only; never edit Steam metadata")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--log", action="store_true")
    command = sub.add_parser("console", help="run a Steam console command through its development bridge")
    command.add_argument("command_args", nargs="+")
    command.add_argument("--wait", type=float, default=1.5)
    args = parser.parse_args()
    try:
        if args.command in ("install", "repair"):
            lines = steamplay.Installation().install(args.steam_app)
            lines += runtime.activate_pending()
        elif args.command == "uninstall":
            lines = steamplay.Installation().uninstall() + migration.migrate()
        elif args.command == "migrate":
            lines = migration.migrate()
        elif args.command == "cloud":
            lines = cloud.prepare(args.appid) if args.operation == 'prepare' else cloud.status(args.appid)
        elif args.command == "sync":
            lines = runtime.activate_pending(dry_run=args.dry_run)
        elif args.command == "console":
            with cef.SteamBridge() as bridge:
                lines = [bridge.console(" ".join(args.command_args), wait=args.wait)]
        else:
            lines = steamplay.Installation().status()
            lines += ["Runtime: " + os.path.realpath(os.path.join(steam.GLUON_HOME, "runtime/current"))]
        if getattr(args, "log", False) and lines:
            path = os.path.join(steam.GLUON_HOME, "logs/agent.log")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a") as handle:
                for line in lines:
                    handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
        else:
            for line in lines:
                print(line)
        return 0
    except (steamplay.SteamPlayError, cloud.CloudError, migration.MigrationError, runtime.RuntimeSelectionError,
            steam.SteamError, cef.BridgeError, OSError, ValueError) as error:
        print("gluon: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
