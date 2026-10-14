#!/bin/sh
# probe stub: record exactly how Steam invokes a compat tool on macOS
L="$HOME/Library/Application Support/Gluon/probe.log"
{ echo "=== $(date) argv:"; for a in "$0" "$@"; do echo "  [$a]"; done; echo "cwd: $(pwd)"; env | grep -iE "^(STEAM|Steam|SteamApp|SteamGame|PROTON|WINE|DYLD|PATH=|LD_)" | sort; } >> "$L" 2>&1
exit 0
