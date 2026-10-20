"""Check the expanded installer layout without touching a user's Steam."""
from pathlib import Path
import json
import sys
import stat
root = Path(sys.argv[1])
manifests = list(root.rglob('steamplay/payloads/*/payload.json'))
assert len(manifests) == 1, 'expected one versioned Steam Play payload'
distribution = (root / 'Distribution').read_text()
assert 'enable_currentUserHome="false"' in distribution and 'enable_localSystem="true"' in distribution, 'Steam integration requires system installation domain'
payload = manifests[0].parent
home = payload.parents[2]
assert (home / 'steamplay/pending.json').is_file()
assert json.loads((home / 'steamplay/pending.json').read_text())['name'] == payload.name
for path in ('bin/gluon', 'bin/gluon-launcher-host', 'bin/gluonctl', 'bin/gluon-agent', 'bin/gluon-uninstall',
             'lib/gluon/appbundle.py', 'lib/gluon/session.py',
             'lib/gluon/steamplay.py', 'lib/gluon/migration.py', 'lib/gluon/package_install.py', 'runtime/pending.json'):
    assert (home / path).is_file(), path
for path in ('lib/gluon/enable.py', 'lib/gluon/metadata.py'):
    assert not (home / path).exists(), 'retired metadata mutator packaged'
manifest = json.loads(manifests[0].read_text())
for path in manifest['files']:
    assert (payload / path).is_file(), path
assert list((payload / 'signatures/macos.arm64').glob('*.json'))
policies = list((home / 'runtime').glob('*/share/gluon/warm-service-policy.json'))
assert len(policies) == 1, 'expected the stock service policy in the runtime fixture'
assert stat.S_IMODE(policies[0].stat().st_mode) == 0o644, 'unprivileged installation must be able to read the policy'
print('Package contains launcher, installer, native library, signatures, licenses and staged runtime')
