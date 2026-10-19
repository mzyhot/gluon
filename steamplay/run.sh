#!/bin/sh
# gluon Steam Play compatibility tool shim
# Embedded by the NotProton-derived dylib. No Steam metadata is edited here.
unset DYLD_INSERT_LIBRARIES
verb="${1:-}"
case "$verb" in
  getcompatpath)
    if [ "$#" -eq 1 ]; then printf '%s\n' "${STEAM_COMPAT_DATA_PATH:-}"; exit 0; fi
    shift ;;
  getnativepath|run|runinprefix|waitforexitandrun) shift ;;
  *) printf 'gluon: unsupported Steam Play verb: %s\n' "$verb" >&2; exit 1 ;;
esac
[ $# -gt 0 ] || { echo 'gluon: no Windows executable supplied' >&2; exit 1; }
gluon_home="${GLUON_HOME:-$HOME/Library/Application Support/Gluon}"
export STEAM_ROOT="${GLUON_STEAM_ROOT:-${STEAM_ROOT:-$HOME/Library/Application Support/Steam}}"
launcher="$gluon_home/bin/gluon"
[ -x "$launcher" ] || { echo "gluon: reinstall Gluon; launcher missing: $launcher" >&2; exit 1; }
tool_directory="$(cd "$(dirname "$0")" && pwd -P)" || exit 1
case "${tool_directory##*/}" in
  gluon)
    PYTHONPATH="$gluon_home/lib" PYTHONDONTWRITEBYTECODE=1 /usr/bin/arch -arm64 /usr/bin/python3 \
      -m gluon.cloud --explain "${STEAM_COMPAT_APP_ID:-${SteamAppId:-0}}" --compat "${STEAM_COMPAT_DATA_PATH:-}" --show-error
    exit 1
    ;;
  gluon-proton)
    [ -n "${STEAM_COMPAT_DATA_PATH:-}" ] || { echo 'gluon: missing Cloud compatibility directory' >&2; exit 1; }
    # The launcher checks the final selected prefix once, after applying the
    # game's settings. The native guard already protected Steam's earlier sync.
    export GLUON_NATIVE_CLOUD=1
    ;;
esac
# Steam references its Windows install-script evaluator under legacycompat. Obtain
# Valve's pinned files on the user's machine, then redirect only these helper paths.
case "$1" in
  */legacycompat/*.exe)
    [ -n "${STEAM_COMPAT_CLIENT_INSTALL_PATH:-}" ] || exit 1
    helper=${1##*/}
    case "$helper" in iscriptevaluator.exe|SteamService.exe) ;; *) echo 'gluon: unknown Steam helper' >&2; exit 1 ;; esac
    PYTHONPATH="$gluon_home/lib" /usr/bin/arch -arm64 /usr/bin/python3 -m gluon.valve_files || exit 1
    shift
    set -- "$gluon_home/steamplay/legacycompat/$helper" "$@"
    ;;
esac
exec "$launcher" "$verb" "$@"
