"""Build a payload inventory from upstream data; no Steam signatures live here."""
import hashlib
import json
from pathlib import Path
import re
import sys

source, payload, repository = map(Path, sys.argv[1:])
builds = {}
for path in sorted((payload / 'signatures/macos.arm64').glob('*.json')):
    data = json.loads(path.read_text())
    builds[str(data['steam_build'])] = {'profile': path.name, 'provenance': 'signature database label'}
latest = max(builds, key=int)
# Database labels identify their creation build. Upstream advertises later clients
# in README: those still require ALL runtime signatures to resolve before hooks.
readme = (source / 'README.md').read_text()
for version in re.findall(r'Steam Client \(version (\d+)\)', readme):
    builds.setdefault(version, {'profile': builds[latest]['profile'], 'provenance': 'pinned upstream README; runtime validation required'})
(payload / 'builds').write_text(''.join(key + '\n' for key in sorted(builds, key=int)))
manifest = {'schema': 1, 'notproton': '8562a6320279224125a80cd66bf8d1af9b04bb2d',
            'dobby': '5dfc8546954ce3b3198132ab13fddb89ee92cdd7',
            'builds': builds, 'files': {}, 'patches': {}}
for path in sorted((repository / 'steamplay/patches').glob('*.patch')):
    manifest['patches'][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
for path in sorted(payload.rglob('*')):
    if path.is_file() and path.name not in ('payload.json', 'dependencies.txt'):
        manifest['files'][str(path.relative_to(payload))] = hashlib.sha256(path.read_bytes()).hexdigest()
(payload / 'payload.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
