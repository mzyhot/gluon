# Native Steam Play integration

The system-domain package requests administrator authorization to stage the
existing Gluon Wine runtime, Python management commands,
and an immutable, versioned Steam Play payload. The installer configures the
selected Steam bundle's `LSEnvironment` to load `gluon-steamplay.dylib`, following
NotProton's implementation. The dylib hooks Steam's compatibility manager,
registers the `windows → macos` Gluon tools and embeds Gluon's launcher shim.
Steam launches `run`, `runinprefix` or `waitforexitandrun`; helpers use the same
per-game prefix, and only game sessions use the Steam bridge and native overlay.

## Installation and recovery

Apple's home-directory installation domain cannot write outside the home folder.
The package therefore stages root-owned files in `/Library/Application Support/Gluon`
and invokes setup as the logged-in console account, dropping privileges before
reading or changing user data. Setup copies verified immutable runtime/payload
versions into that account's Gluon folder. It invokes Steam integration with
ordinary account permissions; administrator authorization does not bypass Steam
bundle ownership checks. Both staging and the per-user runtime copies are retained,
so this currently requires space for both copies. System staging and its receipt
remain after per-user uninstall and support subsequent account deployments.


Package upgrades and `gluonctl install`/`repair` reuse the Steam app recorded by
the previous installation, including apps outside the standard locations. They
do not search for another app when that recorded app is missing. On first install,
`gluonctl install [--steam-app PATH]` discovers a single Steam app through standard
locations and Spotlight, excluding Gluon's restoration backups and temporary
installation bundles. Multiple live copies require an explicit choice; an explicit
`--steam-app PATH` also overrides the recorded selection. The account
must own and be able to write the bundle; its parent must be writable and
owned by that account or root. Steam and Wine must
be stopped. The installer refuses foreign injections and requires an original
Valve signature for a new restoration baseline.

Installation verifies the payload inventory and installed Steam build, copies
and verifies the original bundle, stages the new bundle beside Steam, configures
its environment and signs the dylib, executable and bundle. It restores legacy
Gluon metadata only after staging and signing succeeds. It then publishes the
staged bundle with a journalled rename, checks signing and refreshes
LaunchServices. Failed publication restores the prior bundle. A later repair
recovers an interrupted rename from the transaction receipt.

A clean Valve-signed Steam update establishes a new restoration baseline. Repair
refuses ambiguous independent bundle changes rather than overwriting them.
Uninstall restores the exact original signed bundle, and removes only the Gluon
compatibility tool bearing Gluon's script marker. It retains the restoration
bundles and migration journals under `Gluon/backups/`. Missing tracked Steam apps
or altered backups stop uninstall with an actionable error.

The package script returns failure if integration fails, with details in
`Gluon/logs/install.log`. The staged runtime remains available for a later repair.
A failed package installation is not reported as working native integration.

## Steam compatibility policy

At the pinned revision, signature databases are labelled `1788400362`,
`1789086785` and `1790904859`. The README advertises stable `1791918634` and beta
`1792192922`. These labels record different facts: database creation builds and
upstream's advertised client support. Gluon records their provenance in the
built payload inventory. Advertisement is not end-to-end verification.

The installer reads the installed client package version. Unknown versions are
refused. The dylib rechecks this policy at every Steam startup, so a subsequent
unknown Steam update disables compatibility hooks. Even a declared version must
resolve all required Steam client signatures before compatibility hooks install.
The UI module also requires all its signatures. A partial or zero resolution
installs no hooks for that module. Steam update and crash markers are preserved.
Errors are written to `Gluon/steamplay/steamplay.log`.

Signature patterns may resolve across builds; an older filename alone neither
proves nor disproves newer-build support. Read-only inspection identified the installed stable client as `1791918634`
from its signed-channel manifest. Actual signature resolution and Steam UI
behavior remain required acceptance tests.

## Legacy migration

The old `enable.py` and `metadata.py` modules and their commands are retired.
The installer stops the legacy launch agent before migration. The replacement
agent can only select idle runtime updates.

Migration reads `state/apps.json` and `state/update-policies.json`. It restores
only recorded cache fields whose live values still match Gluon's old edits and
whose Steam change number is unchanged. Newer server entries are retained.
Only exact Gluon launch-option strings are removed. A manifest policy is restored
only while its value still matches Gluon's old setting. Ambiguous fields,
unavailable libraries and non-round-tripping files stop migration. Originals
and before/after journals remain available; a repeat migration is idempotent.
Game folders, prefixes and saves are never removed or copied by migration.
The read-only legacy Cloud recognizer restores a recorded Cloud section only
when the live value is exactly the old mapper's output. Independently changed
Cloud fields still block migration.

Automatic native Cloud uses prefix aliases and Steam's own compatibility
path handling. It never reapplies legacy mappings. See [runtime features](RUNTIME.md)
for preparation, prefix conflict checks and manual acceptance requirements.

## Third-party components

The Steam Play build includes NotProton's client dylib and read-only appinfo
helper, Dobby and cJSON. It excludes NotProton's lsteamclient, steam-shim,
CrossOver runners, Sparkle, fonts, iconmaker and overlay shim. Gluon's existing
optional Steam bridge is unchanged and carries its own licensing requirements.

Some install scripts need Windows helpers absent from macOS Steam. Gluon uses
the pinned upstream Valve download manifest to fetch verified archive members
on the user's machine when such a helper is requested. These proprietary
helpers are not distributed in Gluon's package or source archive. Downloads
must pass archive and individual-member hashes. A failed download stops the
helper with an error. Game launch and these helpers still need real-client tests.

## Verification record

Source editing and syntax inspection are local. Compilation, unit tests,
synthetic Wine launcher tests, native signing fixtures and package inspections
run only in GitHub Actions. The signing fixture compiles a fake Steam executable;
it checks production signing and byte-exact restoration, and does not modify or
execute a real Steam installation. Its Valve identity check is replaced because
a fixture cannot carry Valve's Developer ID. The fixture substitutes its signed
identifier as the identity predicate while exercising production requirement
parsing and rejection of a mismatched identity.

Actual Steam startup, dylib loading, compatibility settings, Windows depot
installation, game execution, overlay, achievements, controls, graphics, audio,
Steam shutdown and Gatekeeper behavior require a suitable real Steam account and
macOS test environment. No such results may be inferred from CI fixtures.
The implementation must not be described as end-to-end verified until those
checks have evidence. The original runtime and graphics regression suites remain
in the full package workflow.
