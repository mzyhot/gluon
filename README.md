# Gluon
Steam Play; for MacOS.
![Gluon slogan](gluon-slogan.svg)
## Layout

    gluon/            Python package (stdlib only)
      vdf.py          Valve KeyValues: text, binary, appinfo/packageinfo readers
      appinfo.py      read/rewrite Steam's app metadata cache
      enable.py       make an app installable + route its Play button to the launcher
      steam.py        Steam paths, running check
      install.py      preserve stale installation records when game folders are missing
      metadata.py     managed-game update protection and metadata refresh detection
      overlay.py      restore Steam's own native overlay at the Wine launch boundary
      runtime.py      select complete runtime updates while Steam and Wine are idle
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

Gluon restores refreshed compatibility metadata while Steam is closed and protects
managed games with Steam's update-on-launch policy. If Steam refreshes an entry
mid-session, Gluon asks for a restart before updating or verifying that game.
Missing installation folders are recovered without removing saves or Wine prefixes.

The plain-Wine launcher preserves Steam's supplied native overlay for the game
host. The overlay comes from the user's installed Steam client.

Runtime builds include extension-checked OpenGL aliases and native Vulkan
portability negotiation through MoltenVK. Packages stage each complete runtime in
a unique directory; the background helper switches the default only while Steam
and Wine are idle, preserving the previous selection for rollback. Game prefixes
stay in place. See the graphics documents for tested capabilities and limitations.
