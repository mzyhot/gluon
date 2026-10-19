#!/bin/sh
# Build the integrated GPL Steam Play payload from pinned public source, in Actions.
set -eu
NOTPROTON_REPO=https://github.com/NotProtonNot/NotProton.git
NOTPROTON_COMMIT=8562a6320279224125a80cd66bf8d1af9b04bb2d
DOBBY_REPO=https://github.com/jmpews/Dobby.git
DOBBY_COMMIT=5dfc8546954ce3b3198132ab13fddb89ee92cdd7
here="$(cd "$(dirname "$0")/.." && pwd)"
. "$here/packaging/require-cloud.sh"
src="$here/build/notproton"
payload="$here/build/steamplay-payload"
fetch() {
    repo="$1" commit="$2" dir="$3"
    [ ! -e "$dir" ] || { echo "Refusing to overwrite source directory $dir" >&2; exit 1; }
    git init -q "$dir"
    git -C "$dir" remote add origin "$repo"
    git -C "$dir" fetch -q --depth 1 origin "$commit"
    git -C "$dir" checkout -q --detach FETCH_HEAD
    [ "$(git -C "$dir" rev-parse HEAD)" = "$commit" ]
}
fetch "$NOTPROTON_REPO" "$NOTPROTON_COMMIT" "$src"
for patch in "$here"/steamplay/patches/*.patch; do
    git -C "$src" apply --check "$patch"
    git -C "$src" apply "$patch"
done
cp "$here/steamplay/run.sh" "$src/dylib/feats/compat_run.sh"
cp "$here/steamplay/build_gate.h" "$src/dylib/gluon_build.h"
cp "$here/steamplay/cloud_guard.c" "$src/dylib/feats/cloud_guard.c"
cp "$here/steamplay/cloud_guard.h" "$src/dylib/feats/cloud_guard.h"
for file in gluon_settings.c gluon_settings.h gluon_settings_js.h; do
    cp "$here/steamplay/$file" "$src/dylib/feats/$file"
done
[ "${1:-}" != --source ] || exit 0
rmdir "$src/vendor/dobby" 2>/dev/null || true
fetch "$DOBBY_REPO" "$DOBBY_COMMIT" "$src/vendor/dobby"
git -C "$src/vendor/dobby" submodule update --init --recursive --depth 1
make -C "$src" dobby
make -C "$src" dylib appinfo
mkdir -p "$here/build/evidence"
clang -arch arm64 -mmacosx-version-min=15.0 -Wall -Wextra -Werror \
    -fsanitize=address,undefined -fno-sanitize-recover=all \
    "$here/steamplay/check-build-gate.c" -o "$here/build/evidence/check-build-gate"
"$here/build/evidence/check-build-gate"
python3 "$here/steamplay/check-cloud-guard.py"
python3 "$here/steamplay/check-settings.py"
# These tests use synthetic fixtures, not an installed Steam client.
# A missing test is an error; upstream Make's optional checks otherwise skip it.
for file in sigcheck.py callscheck.c spawn-env.c activatecheck.c compatsvc-check.c; do
    [ -s "$src/dylib/tests/$file" ]
done
for check in sigcheck callscheck webpatch-fixtures spawn-env activatecheck compatcheck compatsvc-check launch-shell; do
    make -C "$src" -s "$check"
done
mkdir -p "$payload/signatures/macos.arm64" "$payload/licenses" "$here/dist"
cp "$src/out/notproton.dylib" "$payload/gluon-steamplay.dylib"
cp "$src/out/appinfo" "$payload/appinfo"
cp "$here/steamplay/run.sh" "$payload/run"
cp "$src"/signatures/macos.arm64/*.json "$payload/signatures/macos.arm64/"
cp "$src/app/Sources/NotProtonApp/Resources/valve-packages.manifest" "$payload/"
cp "$src/LICENSE" "$payload/licenses/NotProton-LICENSE"
cp "$src/NOTICE" "$payload/licenses/NotProton-NOTICE"
cp "$src/vendor/dobby/LICENSE" "$payload/licenses/Dobby-LICENSE"
cp "$src/vendor/cJSON.h" "$payload/licenses/cJSON-header.txt"
# Preserve notices of Dobby's compiled external dependencies as well.
python3 - "$src/vendor/dobby" "$payload/licenses/dobby-dependencies" <<'LICENSES'
from pathlib import Path
import shutil
import sys
source, destination = map(Path, sys.argv[1:])
for path in source.rglob('*'):
    if path.is_file() and path.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')) and '.git' not in path.parts:
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
LICENSES
cp "$here/LICENSE" "$payload/licenses/Gluon-LICENSE"
printf 'gluon-proton\t1\trosetta\tGluon\ngluon\t1\trosetta\tGluon\n' > "$payload/tools"
codesign -f -s - "$payload/gluon-steamplay.dylib" "$payload/appinfo"
python3 "$here/steamplay/manifest.py" "$src" "$payload" "$here"
lipo "$payload/gluon-steamplay.dylib" -verify_arch arm64 x86_64
codesign --verify --strict "$payload/gluon-steamplay.dylib"
codesign --verify --strict "$payload/appinfo"
otool -L "$payload/gluon-steamplay.dylib" > "$payload/dependencies.txt"
if grep -E '^[[:space:]]+/(opt|Users|private)/|CrossOver' "$payload/dependencies.txt"; then
    echo 'Non-system Steam Play dependency' >&2; exit 1
fi
# Corresponding source, including Dobby's pinned submodules and original notices.
# No proprietary Steam binaries are fetched or included.
tar -czf "$here/dist/Gluon-SteamPlay-source.tar.gz" --exclude=.git --exclude=out --exclude=build \
    -C "$here/build" notproton
