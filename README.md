# Gluon

Steam Play for macOS, using Gluon's Wine runtime and an integrated, GPLv3
NotProton-derived Steam client library. CrossOver and a separate NotProton
installation are unnecessary.

This branch replaces the old metadata workaround. Steam's compatibility manager
selects the Windows depots and launches Gluon. `appinfo.vdf`, launch options and
manifest update policies are changed only once to restore recorded legacy Gluon
changes during migration. No metadata synchronization runs afterward.

The implementation is experimental. See [verification status](docs/STEAMPLAY.md)
for the distinction between CI fixtures, builds and actual Steam/game tests.

Install the Actions-built `.pkg` while Steam and Wine are closed. The per-user
installer needs a Steam application owned by the installing account and a writable
parent directory owned by that account or root. For a custom location or multiple Steam copies, use:

```sh
"$HOME/Library/Application Support/Gluon/bin/gluonctl" install --steam-app "$HOME/Applications/Steam.app"
```

The installer stages and ad-hoc signs a modified Steam bundle, verifies it, and
keeps a verified copy of the original signed bundle for restoration. Ad-hoc
signing is local signing; it is not Developer ID signing or Apple notarization.
Gatekeeper and Steam Input behavior still require real-machine verification.

Restart Steam, then install and play Windows games normally. The compatibility
hooks default unmapped Windows-only games to Gluon. Steam's Compatibility
settings provide tool overrides. Hook availability depends on the Steam build
and successful runtime signature validation.

```sh
"$HOME/Library/Application Support/Gluon/bin/gluonctl" status
"$HOME/Library/Application Support/Gluon/bin/gluonctl" repair
"$HOME/Library/Application Support/Gluon/bin/gluon-uninstall"
```

Repair requires Steam and Wine to be closed. Unsupported builds fail with a
compatibility error. Foreign injections and independent bundle changes are
preserved and reported. Recovery retains originals, migration journals, game
files, Wine prefixes and saves. Reconnect any tracked removable Steam application
before uninstalling. Steam updates that restore a clean Valve-signed bundle can
be integrated again; ambiguous modifications require restoring the saved bundle.

The launcher retains Gluon's graphics, media, Steam API bridge and overlay paths.
Plain Wine prefixes continue to use `steamapps/compatdata/<appid>/pfx-wine`.
Runtime packages are staged under unique names and activated only while Steam
and Wine are idle. The remaining background helper performs runtime selection.
It never enables games or edits Steam metadata.

Builds and tests run through GitHub Actions. The **Steam Play** workflow compiles
the pinned library, runs upstream synthetic checks, tests actual signing with a
fake Steam bundle, and inspects a package fixture. The **Build** workflow combines
that payload with the complete Wine runtime and publishes the `.pkg` and Steam
Play corresponding source archive. Development builds are disabled locally.

Source: [NotProton](https://github.com/NotProtonNot/NotProton), pinned at
`8562a6320279224125a80cd66bf8d1af9b04bb2d`, and
[Dobby](https://github.com/jmpews/Dobby), pinned at
`5dfc8546954ce3b3198132ab13fddb89ee92cdd7`. Gluon's modifications live in
`steamplay/patches/`. Signature data and Valve's download manifest are fetched
from that exact upstream commit during CI; Steam addresses and binary hashes
are not checked into this repository. See [NOTICE.md](NOTICE.md) for licenses.

Gluon is independent of Valve, CodeWeavers and Apple. Game compatibility varies.
