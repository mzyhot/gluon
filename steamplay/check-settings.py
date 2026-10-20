#!/usr/bin/env python3
"""Run production global-policy/settings fixtures on disposable CI Steam data."""
import os
import json
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
                                  '1': {'executable': mac, 'config': configuration}}},
            'depots': {'100': {'config': {'oslist': 'macos'}, 'manifests': {'public': {'gid': '1000'}}},
                       '101': {'config': {'oslist': 'windows'}, 'manifests': {'public': {'gid': '2000'}}},
                       '102': {'manifests': {'public': {'gid': '3000'}}},
                       'branches': {'public': {'buildid': '4000'}}}}

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
               15: app(arch='32'), 17: app(common={'osarch':'32'}),
               18: app(mac='Game.sh'), 19: app(mac='Game.sh'),
               20: app(mac='Game.sh'), 21: app(common={'osarch':'64'}),
               22: app(), 23: app(mac='Game.sh'), 24: app(mac='Game.sh'),
               25: app(mac='Game.sh'), 26: app(mac='Game.sh'), 27: app(mac='Game.sh'),
               28: app(mac='Game.sh'), 29: app(mac='Game.sh'),
               30: app(mac='Game.sh'), 31: app(mac='Game.sh'),
               32: app(mac='Game.sh'), 33: app()}
    # A shared/Windows declaration is not a declaration about the Mac binary.
    entries[22]['depots']['102']['config'] = {'osarch':'64'}
    entries[33]['depots']['100']['config']['osarch'] = '32'  # A content subset cannot prove an uninstalled launch.
    for appid in (18,23):
        entries[appid]['depots']['103'] = {'config':{'lowviolence':'1'}, 'manifests':{'public':{'gid':'4000'}}}

    # Original generated metadata reproduces Spacewar's inaccurate common
    # platform flags plus its sole implicit Windows executable launch.
    def windows_launch_only():
        return {'common':{'type':'Game','oslist':'windows,macos,linux','osarch':'','osextended':''},
                'config':{'launch':{'0':{'executable':'Example.exe','type':'none'}}},
                'depots':{'100':{'manifests':{'public':{'gid':'1000'}}},
                          '101':{'config':{'oslist':'windows'},'depotfromapp':'999'}}}
    for appid in range(34,43):
        entries[appid] = windows_launch_only()
    entries[35]['config']['launch']['1'] = {'executable':'Native.app','config':{'oslist':'macos'}}
    entries[36]['common']['osextended'] = 'macos64'
    entries[37]['depots']['100']['config'] = {'oslist':'macos','osarch':'32'}
    entries[38]['config']['launch']['0']['executable'] = 'Native.app'
    entries[39]['config']['launch']['1'] = {'executable':'linux-game','config':{'oslist':'linux'}}
    entries[40]['config']['launch']['0']['config'] = {'oslist':'all'}
    entries[41]['config']['launch']['1'] = {'executable':'Native.app','config':{'oslist':'macos','osarch':'arm64','BetaKey':'beta'}}
    entries[42]['depots']['100'] = {'depotfromapp':'998'} # Unresolved native/shared platform is ambiguous.
    entries[13]['config']['launch'].pop('0')  # No Windows build to run.
    entries[15]['config']['launch']['2'] = {'executable':'Game64.app', 'config':{'oslist':'macos','osarch':'64'}}
    macho32 = struct.pack('<IIIIIII', 0xfeedface, 7, 3, 2, 0, 0, 0)
    macho64 = struct.pack('<IIIIIIII', 0xfeedfacf, 0x01000007, 3, 2, 0, 0, 0, 0)
    fat = struct.pack('>II', 0xcafebabe, 2) + struct.pack('>IIIII', 7, 3, 48, 28, 0) + struct.pack('>IIIII', 0x01000007, 3, 76, 32, 0) + macho32 + macho64
    for appid, binary in ((8,macho32),(9,macho64),(10,fat),(11,b'not a Mach-O'),(12,macho32),(18,macho32),(19,macho64),(20,macho32),(21,macho32),(22,macho32),(23,macho32),(24,macho32),(25,macho32),(26,macho32),(27,macho32),(28,macho32),(29,macho32),(30,macho32),(31,macho32),(32,macho32)):
        install = library / ('steamapps/common/Game%d' % appid)
        target = install / 'Game.app/Contents/MacOS/Game'
        target.parent.mkdir(parents=True)
        target.write_bytes(binary)
        (target.parents[1] / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'Game'}))
        if appid == 12:  # Arbitrary scripts are not mistaken for native binaries.
            (install / 'stub.sh').write_text('#!/bin/sh\nexit 0\n')
        (library / ('steamapps/appmanifest_%d.acf' % appid)).write_text('"AppState" { "installdir" "Game%d" "InstalledDepots" { "100" { "manifest" "1000" } "102" { "manifest" "3000" } } }' % appid)
        if appid in (18,19,20,23,24,25,26,27,28,29,30,31,32):
            # Reproduce the Valve wrapper layout with original synthetic text;
            # no game script/binary or personal metadata is copied into CI.
            (install/'Game_osx').write_bytes(binary)
            wrapper = '#!/bin/bash\nGAMEROOT=$(cd "${0%/*}" && echo "$PWD")\nUNAME=$(uname)\nif [ "$UNAME" = Darwin ]; then\n GAMEEXE=Game_osx\nelse\n GAMEEXE=Game_linux\nfi\n"${GAMEROOT}"/${GAMEEXE} "$@"\n'
            if appid == 20:
                wrapper = wrapper.replace('GAMEEXE=Game_osx','GAMEEXE="$(choose_binary)"')
            if appid == 24:
                (install/'Game_osx').unlink()
            if appid == 25:
                (install/'Game64').write_bytes(macho64)
                wrapper = wrapper.replace(' GAMEEXE=Game_osx',' GAMEEXE=Game_osx\n GAMEEXE=Game64')
            if appid == 26:
                wrapper = '#!/bin/sh\n# An unrelated Mach-O in this directory is not launch proof.\nexit 0\n'
            if appid == 27:
                wrapper = wrapper.replace(' GAMEEXE=Game_osx',' GAMEEXE=Game_osx\n GAMEEXE+=64')
            if appid == 28:
                wrapper = wrapper.replace(' GAMEEXE=Game_osx',' GAMEEXE=Game_osx\n read GAMEEXE')
            if appid == 29:
                (install/'Game64').write_bytes(macho64)
                wrapper = wrapper.replace('"${GAMEROOT}"/${GAMEEXE} "$@"', '# "${GAMEROOT}"/${GAMEEXE} "$@"\n./Game64 "$@"')
            if appid == 30:
                wrapper = wrapper.replace('"${GAMEROOT}"/${GAMEEXE} "$@"', 'if [ -z $GAMEEXE ]; then\n GAMEEXE=Game_osx\nfi\necho $DEBUGGER -x "$ARGSFILE" $DEBUGGER_ARGS --args "${GAMEROOT}/${GAMEEXE}" "$@"\n$DEBUGGER -x "$ARGSFILE" $DEBUGGER_ARGS --args "${GAMEROOT}/${GAMEEXE}" "$@"\n${DEBUGGER} -s "$ARGSFILE" -- "${GAMEROOT}"/${GAMEEXE} "$@"\n${DEBUGGER} "${GAMEROOT}"/${GAMEEXE} "$@"')
            if appid == 31:
                wrapper = wrapper.replace('GAMEROOT=$(cd "${0%/*}" && echo "$PWD")','GAMEROOT=/other/native64/game')
            if appid == 32:
                wrapper = wrapper.replace(' GAMEEXE=Game_osx',' GAMEEXE=Game_osx\n . ./choose-target.sh')
            (install/'Game.sh').write_text(wrapper)
            if appid == 23:
                (library/('steamapps/appmanifest_%d.acf' % appid)).write_text('"AppState" { "installdir" "Game%d" "InstalledDepots" { "100" { "manifest" "1000" } "102" { "manifest" "3000" } "103" { "manifest" "3999" } } }' % appid)
    cache = steam / 'appcache/appinfo.vdf'
    helper_env = dict(os.environ, GLUON_HOME=str(home))
    cache.write_bytes(app_cache(entries))
    output = subprocess.check_output([str(source / 'out/appinfo'), str(cache), '--platforms'], text=True, env=helper_env)
    lists = dict(line.split('=',1) for line in output.splitlines())
    assert lists['mac32'] == '1,2,8,18,21,22,30', output
    assert lists['windows_launch_only'] == '34,39', output
    # A native64 executable vetoes metadata32 too, including universal binaries.
    entries[9]['config']['launch']['1']['config']['osarch'] = '32'
    entries[10]['config']['launch']['1']['config']['osarch'] = '32'
    cache.write_bytes(app_cache(entries))
    output = subprocess.check_output([str(source / 'out/appinfo'), str(cache), '--platforms'], text=True, env=helper_env)
    assert dict(line.split('=',1) for line in output.splitlines())['mac32'] == '1,2,8,18,21,22,30', output
    # Optional violence content may legitimately be omitted (Half-Life uses
    # this layout). If present it must attest its current version as usual.
    assert '18' in json.loads((home/'state/mac32-evidence.json').read_text())['apps']
    (library/'steamapps/common/Game18/Game_osx').unlink()
    output = subprocess.check_output([str(source/'out/appinfo'),str(cache),'--platforms'],text=True,env=helper_env)
    assert '18' in dict(line.split('=',1) for line in output.splitlines())['mac32'].split(','), 'Wrapper architecture evidence did not survive Windows depot replacement'
    wrapper18 = library/'steamapps/common/Game18/Game.sh'
    original_wrapper = wrapper18.read_text()
    wrapper18.write_text(original_wrapper.replace('GAMEEXE=Game_osx','GAMEEXE="$(choose_binary)"'))
    output = subprocess.check_output([str(source/'out/appinfo'),str(cache),'--platforms'],text=True,env=helper_env)
    assert '18' not in dict(line.split('=',1) for line in output.splitlines())['mac32'].split(','), 'Dynamic wrapper retained an old native proof'
    wrapper18.write_text(original_wrapper)
    (library/'steamapps/common/Game18/Game_osx').write_bytes(macho32)
    output = subprocess.check_output([str(source/'out/appinfo'),str(cache),'--platforms'],text=True,env=helper_env)
    assert '18' in dict(line.split('=',1) for line in output.splitlines())['mac32'].split(',')
    (library/'steamapps/common/Game18/Game_osx').write_bytes(macho64)
    output = subprocess.check_output([str(source/'out/appinfo'),str(cache),'--platforms'],text=True,env=helper_env)
    assert '18' not in dict(line.split('=',1) for line in output.splitlines())['mac32'].split(','), 'A wrapper upgrade to native64 retained old32 evidence'
    (library/'steamapps/common/Game18/Game_osx').write_bytes(macho32)
    output = subprocess.check_output([str(source/'out/appinfo'),str(cache),'--platforms'],text=True,env=helper_env)
    assert '18' in dict(line.split('=',1) for line in output.splitlines())['mac32'].split(',')
    entries[18]['config']['launch']['1']['config']['osarch'] = 'arm64'
    cache.write_bytes(app_cache(entries))
    output = subprocess.check_output([str(source/'out/appinfo'),str(cache),'--platforms'],text=True,env=helper_env)
    assert '18' not in dict(line.split('=',1) for line in output.splitlines())['mac32'].split(','), 'Mac-specific arm64 must revoke wrapper evidence'
    # Each helper call is a fresh process, matching refreshes and Steam restarts.
    # Replacing the Mac depot with Windows must not erase the only Mac32 proof.
    native = library/'steamapps/common/Game8/Game.app/Contents/MacOS/Game'
    selected_branch = 'public'
    def install_native():
        native.write_bytes(macho32)
        versions = []
        for did in ('100','102'):
            depot = entries[8]['depots'].get(did,{})
            if 'depotfromapp' in depot:
                depot = entries[int(depot['depotfromapp'])]['depots'][did]
            gid = depot.get('manifests',{}).get(selected_branch,{}).get('gid')
            if gid: versions.append('"%s" { "manifest" "%s" }' % (did,gid))
        (library/'steamapps/appmanifest_8.acf').write_text('"AppState" { "installdir" "Game8" "UserConfig" { "BetaKey" "%s" } "InstalledDepots" { %s } }' % (selected_branch,' '.join(versions)))
    state = home/'state/mac32-evidence.json'
    assert '8' in json.loads(state.read_text())['apps']
    native.unlink()
    (library/'steamapps/appmanifest_8.acf').write_text('"AppState" { "installdir" "Game8" "InstalledDepots" { "101" { "manifest" "2000" } } }')
    def classified():
        cache.write_bytes(app_cache(entries))
        result = subprocess.check_output([str(source/'out/appinfo'),str(cache),'--platforms'],text=True,env=helper_env)
        return set(dict(line.split('=',1) for line in result.splitlines())['mac32'].split(','))
    assert '8' in classified(), 'Windows depot replacement lost positive Mac32 proof'
    entries[8]['depots']['101']['manifests']['public']['gid'] = '2001'
    entries[8]['depots']['branches']['public']['buildid'] = 'unrelated-buildid'
    assert '8' in classified(), 'Windows-only updates must not invalidate the Mac branch signature'
    entries[8]['depots']['100']['manifests']['public']['gid'] = '1001'
    assert '8' not in classified(), 'Changed Mac depot manifest reused stale architecture proof'
    assert '8' not in json.loads(state.read_text())['apps']
    native.write_bytes(macho32) # An old, untracked Mac file is not the new depot version.
    assert '8' not in classified(), 'Old Mac bytes were relabeled with a newer Mac manifest'
    install_native()
    assert '8' in classified()
    native.unlink()
    entries[8]['depots']['102']['manifests']['public']['gid'] = '3001'
    assert '8' not in classified(), 'Changed shared depot manifest reused stale proof'
    install_native()
    assert '8' in classified()
    native.unlink()
    entries[8]['config']['launch']['1']['arguments'] = '--new-mac-launch'
    assert '8' not in classified(), 'Changed Mac launch config reused stale proof'
    install_native()
    assert '8' in classified()
    native.unlink()
    entries[8]['common']['osextended'] = 'macos64'
    assert '8' not in classified(), 'Mac64 declaration must immediately veto remembered Mac32'
    entries[8]['common'].pop('osextended')
    assert '8' not in classified(), 'Revoked Mac32 proof survived a Mac64 declaration'
    install_native()
    assert '8' in classified()
    native.write_bytes(macho64)
    assert '8' not in classified(), 'Actual native64 executable must revoke remembered proof'
    native.unlink()
    assert '8' not in classified(), 'Revoked proof returned after the native64 executable was removed'
    # No stable Mac version identity means no durable automatic selection.
    install_native()
    entries[8]['depots'] = {'101': {'config': {'oslist': 'windows'}, 'manifests': {'public': {'gid':'1'}}}}
    assert '8' not in classified(), 'Unversioned Mac proof must not disappear after platform switching'
    # Referenced shared depots inherit their Mac/shared manifest identity from
    # the referenced cached app, rather than silently omitting that version.
    entries[8]['depots'] = app()['depots']
    entries[8]['depots']['102'] = {'depotfromapp': '16'}
    entries[16] = {'common': {'type':'DLC','oslist':'macos'}, 'depots': {'102': {'manifests': {'public': {'gid':'5000'}}}}}
    install_native() # This new referenced Mac/shared version must be installed before attesting it.
    assert '8' in classified()
    native.unlink()
    assert '8' in classified()
    entries[16]['depots']['102']['manifests']['public']['gid'] = '5001'
    assert '8' not in classified(), 'Referenced shared depot update reused stale proof'
    # Switching branch changes the proof key even when launch paths are reused.
    install_native()
    entries[8]['depots']['100']['manifests']['beta'] = {'gid':'1002'}
    entries[16]['depots']['102']['manifests']['beta'] = {'gid':'5002'}
    assert '8' in classified()
    native.unlink()
    game_manifest = library/'steamapps/appmanifest_8.acf'
    selected_branch = 'beta'
    game_manifest.write_text('"AppState" { "installdir" "Game8" "UserConfig" { "BetaKey" "beta" } }')
    assert '8' not in classified(), 'A new Mac beta branch reused the old branch proof'
    # A Mac-specific architecture declaration in a depot also revokes cached32.
    install_native()
    assert '8' in classified()
    native.unlink()
    entries[8]['depots']['100']['config']['osarch'] = '64'
    assert '8' not in classified()
    entries[8]['depots']['100']['config'].pop('osarch')
    assert '8' not in classified()
    # Partial/corrupt helper state is ignored safely rather than disabling the
    # rest of the catalogue; metadata-declared32 remains available.
    state.write_text('{broken')
    assert '1' in classified() and '8' not in classified()
    # A newly observed executable must not trigger a depot replacement unless
    # its proof can actually be saved first.
    state.unlink()
    state.mkdir()
    install_native()
    assert '8' not in classified(), 'Unsaved proof was exposed before a Windows depot replacement'
    state.rmdir()
    # Bound the persistent catalogue while retaining a freshly observed proof.
    state.write_text(json.dumps({'version':1,'apps':{str(10000+i): {'signature':'0'*64,'checked':0} for i in range(4096)}}))
    assert '8' in classified()
    retained = json.loads(state.read_text())['apps']
    assert len(retained) <= 4096 and '8' in retained
    native.unlink()
    assert '8' in classified()
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
static struct tool { const char *name; } primary={"gluon-proton"}, recovery={"gluon"}, other={"other-tool"}, prefixed_other={"gluon-custom"};
static void *orig_FindToolForTargetApp, *original_tool;
static const char *mapping;
static unsigned platforms, cloud_checks;
static int auto_mac32, windows_launch_only;
static void *fixture_original(void *mgr, uint32_t appid) { (void)mgr; (void)appid; return original_tool; }
static unsigned np_compat_valid_platforms(void *mgr, uint32_t appid) { (void)mgr; (void)appid; return platforms; }
static int gluon_settings_auto_app(uint32_t appid) { return auto_mac32 && appid == 123; }
static int np_nativeplatform_is_windows_launch_only(uint32_t appid) { return windows_launch_only && appid == 480; }
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
