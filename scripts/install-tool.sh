#!/bin/sh
# Development installation uses the same verified payload and installer as the pkg.
set -eu
here="$(cd "$(dirname "$0")/.." && pwd)"
PYTHONPATH="$here" exec /usr/bin/arch -arm64 /usr/bin/python3 -m gluon install "$@"
