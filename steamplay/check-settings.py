#!/usr/bin/env python3
"""Run production global-policy/settings fixtures on disposable CI Steam data."""
import os
from pathlib import Path
import plistlib
import struct
import subprocess
import sys
import tempfile

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'darwin':
    raise SystemExit('Steam Settings fixtures run only on GitHub macOS runners')
root = Path(__file__).resolve().parents[1]
source = root / 'build/notproton'
sys.path.insert(0, str(root))
from gluon import vdf

def app_cache(entries):
    out = bytearray(struct.pack('<II', 0x07564428, 1))
    for appid, app in entries.items():
        blob = vdf.binary_dumps({'appinfo': app})
        head = struct.pack('<IIQ', 0, 0, 0) + b'0' * 20 + struct.pack('<I', 1) + b'0' * 20
        out += struct.pack('<II', appid, len(head) + len(blob)) + head + blob
    return out + b'\0' * 4

def app(mac='Game.app', arch=None, common=None):
    configuration = {'oslist': 'macos'}
    if arch is not None:
        configuration['osarch'] = arch
    return {'common': {'type': 'Game', 'oslist': 'windows,macos', **(common or {})},
            'config': {'launch': {'0': {'executable': 'Game.exe', 'config': {'oslist': 'windows'}},
                                  '1': {'executable': mac, 'config': configuration}}}}

with tempfile.TemporaryDirectory() as temporary:
    base = Path(temporary)
    steam, library, home = base / 'Steam', base / 'SecondLibrary', base / 'Gluon'
    for path in (steam / 'appcache', steam / 'steamapps', library / 'steamapps/common', home / 'steamplay'):
        path.mkdir(parents=True)
    (steam / 'steamapps/libraryfolders.vdf').write_text('"libraryfolders" { "0" { "path" "%s" } }' % library)
    entries = {1: app(arch='32'), 2: app(arch=32), 3: app(arch='64'),
               4: app(arch='32', common={'osextended': 'macos64'}),
               5: app(arch='32', common={'osextended': 'macosapplesilicon'}),
               6: app(), 7: app(mac='Windows.exe', arch='32'),
               8: app(), 9: app(), 10: app(), 11: app(), 12: app(mac='stub.sh'),
               13: app(arch='32'), 14: app(mac='../escape', arch=None),
               15: app(arch='32')}
    entries[13]['config']['launch'].pop('0')  # No Windows build to run.
    entries[15]['config']['launch']['2'] = {'executable':'Game64.app', 'config':{'oslist':'macos','osarch':'64'}}
    macho32 = struct.pack('<IIIIIII', 0xfeedface, 7, 3, 2, 0, 0, 0)
    macho64 = struct.pack('<IIIIIIII', 0xfeedfacf, 0x01000007, 3, 2, 0, 0, 0, 0)
    fat = struct.pack('>II', 0xcafebabe, 2) + struct.pack('>IIIII', 7, 3, 48, 28, 0) + struct.pack('>IIIII', 0x01000007, 3, 76, 32, 0) + macho32 + macho64
    for appid, binary in ((8,macho32),(9,macho64),(10,fat),(11,b'not a Mach-O'),(12,macho32)):
        install = library / ('steamapps/common/Game%d' % appid)
        target = install / 'Game.app/Contents/MacOS/Game'
        target.parent.mkdir(parents=True)
        target.write_bytes(binary)
        (target.parents[1] / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'Game'}))
        if appid == 12:  # Arbitrary scripts are not mistaken for native binaries.
            (install / 'stub.sh').write_text('#!/bin/sh\nexit 0\n')
        (library / ('steamapps/appmanifest_%d.acf' % appid)).write_text('"AppState" { "installdir" "Game%d" }' % appid)
    cache = steam / 'appcache/appinfo.vdf'
    cache.write_bytes(app_cache(entries))
    output = subprocess.check_output([str(source / 'out/appinfo'), str(cache), '--platforms'], text=True)
    lists = dict(line.split('=',1) for line in output.splitlines())
    assert lists['mac32'] == '1,2,8', output
    # A native64 executable vetoes metadata32 too, including universal binaries.
    entries[9]['config']['launch']['1']['config']['osarch'] = '32'
    entries[10]['config']['launch']['1']['config']['osarch'] = '32'
    cache.write_bytes(app_cache(entries))
    output = subprocess.check_output([str(source / 'out/appinfo'), str(cache), '--platforms'], text=True)
    assert dict(line.split('=',1) for line in output.splitlines())['mac32'] == '1,2,8', output
    executable = base / 'settings-check'
    subprocess.run(['clang','-std=c17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
        '-fno-sanitize-recover=all','-I'+str(source/'dylib/feats'),str(root/'steamplay/check-settings.c'),
        str(source/'dylib/feats/gluon_settings.c'),str(source/'dylib/util/file.c'),'-o',str(executable)],check=True)
    env = dict(os.environ, GLUON_HOME=str(home))
    for operation in ('on','off','read-off'):
        subprocess.run([str(executable),operation],env=env,check=True)
    assert (home/'steamplay/auto-mac32').read_text() == '0\n'
    env['GLUON_HOME'] = str(base/'does-not-exist')
    subprocess.run([str(executable),'unwritable'],env=env,check=True)
    # Compile the actual resolver policy and hook against a tiny fake manager.
    # No client structures, proprietary binaries or installed Steam are involved.
    compat_source = (source/'dylib/feats/compat.c').read_text()
    force = compat_source.split('bool np_compat_would_force(',1)[1].split('\ntypedef ',1)[0]
    hook_source = (source/'dylib/hooks/hook_compat.c').read_text()
    hook = hook_source.split('static void *orig_FindToolForTargetApp = NULL;',1)[1].split('static void *orig_WindowsOnlyLookup',1)[0]
    prelude = r'''#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#define COMPAT_TOOL_NAME_OFF 0
#define COMPAT_PLATFORM_WINDOWS 1
#define COMPAT_PLATFORM_MACOS 2
#define NP_COMPAT_TOOL_NONE "gluon.none"
#define NP_ERR(...) ((void)0)
#define NP_DBG(...) ((void)0)
static struct tool { const char *name; } primary={"gluon-proton"}, recovery={"gluon"}, other={"other-tool"};
static void *orig_FindToolForTargetApp, *original_tool;
static const char *mapping;
static unsigned platforms, cloud_checks;
static int auto_mac32;
static void *fixture_original(void *mgr, uint32_t appid) { (void)mgr; (void)appid; return original_tool; }
static unsigned np_compat_valid_platforms(void *mgr, uint32_t appid) { (void)mgr; (void)appid; return platforms; }
static int gluon_settings_auto_app(uint32_t appid) { return auto_mac32 && appid == 123; }
static const char *np_compat_app_mapping(void *mgr, uint32_t appid) { (void)mgr; (void)appid; return mapping; }
static void *np_compat_registered_tool(void *mgr) { (void)mgr; return &primary; }
static void *np_compat_recovery_tool(void *mgr) { (void)mgr; return &recovery; }
static void *gluon_cloud_select(uint32_t appid, void *tool, void *fallback) { (void)appid; (void)fallback; cloud_checks++; return tool; }
'''
    routing_source = base/'routing.c'
    routing_source.write_text(prelude + '\nbool np_compat_would_force(' + force + '\n' + hook + (root/'steamplay/check-settings-routing.c').read_text())
    routing_executable = base/'routing'
    subprocess.run(['clang','-std=c17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all',str(routing_source),'-o',str(routing_executable)],check=True)
    subprocess.run([str(routing_executable)],check=True)
    # Execute the actual compiled C-emitted bootstrap and getter. Decoding C
    # string literals in Python would miss preprocessing transformations.
    emitter = base/'emit-settings'
    subprocess.run(['clang','-std=c17','-Wall','-Wextra','-Werror','-Werror=trigraphs',
                    str(root/'steamplay/emit-settings.c'),'-o',str(emitter)],check=True)
    emitted, emitted_getter = base/'settings.js', base/'invalid-os-getter.js'
    subprocess.run([str(emitter),str(emitted),str(emitted_getter)],check=True)
    subprocess.run(['node',str(root/'steamplay/check-settings.js'),str(emitted),str(emitted_getter)],check=True)
print('Steam Settings architecture, persistence and product UI fixtures passed')
