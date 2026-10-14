# Gluon
Steam Play; for MacOS.
<p align="center">
  <img src="gluon-slogan.svg" alt="Gluon slogan" width="100%">
</p>
## Layout

    gluon/            Python package (stdlib only)
      vdf.py          Valve KeyValues: text, binary, appinfo/packageinfo readers
      appinfo.py      read/rewrite Steam's app metadata cache
      enable.py       make an app installable + route its Play button to the launcher
      steam.py        Steam paths, running check
      cef.py          Steam console over the client's debug bridge (development aid)
    tool/             what gets installed into Steam's compatibilitytools.d/gluon
      gluon           launcher Steam starts in place of the game's .exe
    scripts/          install-tool.sh (development copy of the launcher inside Steam)
    packaging/        build-runtime.sh (runtime from source), build-pkg.sh, package scripts
    docs/NOTES.md     findings, open items, machine state
    archive/          retired experiments, kept for reference

## Installer package

    ./packaging/build-runtime.sh         # Windows runtime, from open-source code -> build/gluon-wine
    GLUON_PKG_RUNTIME=build/gluon-wine ./packaging/build-pkg.sh 0.3.0

Releases are built by GitHub Actions (`.github/workflows/build.yml`).

Installing it is all a user does: a background helper then keeps every Windows-only game
in the library installable and routed through Gluon. See docs/NOTES.md, "Package".

## Development use

    ./scripts/install-tool.sh            # copy tool/ into Steam
    python3 -m gluon candidates          # cached games with a Windows build but no macOS build
    python3 -m gluon enable <appid>      # Steam must be closed
    python3 -m gluon status
    python3 -m gluon disable <appid>     # restore original metadata and launch option

The launcher uses the runtime at `~/Library/Application Support/Gluon/runtime/current`
(or `$GLUON_RUNTIME`): either a plain Wine tree or a Proton-layout Steam Play tool.
It logs to `~/Library/Application Support/Gluon/logs/`.
