#!/usr/bin/env python3
"""Exercise the exact warm-session lifecycle helpers, without starting Wine.

Compilation/execution only runs on GitHub Actions. Patched Wine process.c methods
are extracted unchanged; tiny server-object stubs make lifetime, flush ordering,
child accounting, busy shutdown and bounded startup expiry observable.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import tempfile


def function(text, name):
    pattern = r'^[^\n]*\b' + re.escape(name) + r'\([^;]*?\)\n\{.*?^\}'
    found = re.findall(pattern, text, re.M | re.S)
    # A line prefix may not consume an earlier function through DOTALL.
    if len(found) != 1:
        raise ValueError('expected one warm-session helper: ' + name)
    return found[0]


def extract(root):
    process = (root / 'server/process.c').read_text()
    request = (root / 'server/request.c').read_text()
    main = (root / 'server/main.c').read_text()
    names = ('init_gluon_session_timeout', 'shutdown_gluon_idle_session', 'process_died', 'add_process_thread')
    methods = '\n\n'.join(function(process, name) for name in names)
    cleanup = function(process, 'process_died')
    if not cleanup.index('release_object( process );') < cleanup.index('flush_registry();') < cleanup.index('set_gluon_user_active( 0 );'):
        raise ValueError('registry must flush before the user-idle lock is released')
    create = function(process, 'create_process')
    if not create.index('if (shutdown_stage)') < create.index('alloc_object( &process_ops )'):
        raise ValueError('idle shutdown must refuse pending first-client creation')
    control = function(request, 'request_gluon_idle_shutdown')
    if 'kill(' in control or 'kill( ' in control or 'SIGUSR' in control:
        raise ValueError('idle-only control must never signal a PID')
    for required in ('SOCK_DGRAM', 'open_gluon_session_lock', 'poll( &pollfd, 1, 1000 )', "result == '2'"):
        if required not in control:
            raise ValueError('missing bounded native idle-control boundary: ' + required)
    for required in ('fl.l_start = 1;', 'fl.l_start = 2;', 'fl.l_pid != pid', 'F_SETLKW',
                     'open_gluon_control_socket();', 'if (gluon_session_mode) unlink( "gluon-control" );'):
        if required not in request:
            raise ValueError('missing native warm protocol boundary: ' + required)
    if 'seconds < 1 || seconds > 120' not in main or 'init_gluon_session_timeout();' not in main:
        raise ValueError('warm startup and lifetime must be bounded')
    return methods


PREAMBLE = r'''
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
struct object { int ignored; };
struct process { unsigned int id; int is_system, running_threads, thread_list, entry; };
struct thread { int proc_entry; };
struct timeout_user { int ignored; };
static int gluon_session_mode, user_processes, running_processes, shutdown_stage;
static long long master_socket_timeout;
#define TIMEOUT_INFINITE 0x7fffffffffffffffLL
static struct timeout_user timer;
static struct timeout_user *shutdown_timeout;
static int debug_level, process_list, shutdown_calls;
static char events[64];
static unsigned event_count;
static void event(char value) { assert(event_count + 1 < sizeof(events)); events[event_count++] = value; }
static void server_shutdown_timeout(void *arg) { (void)arg; }
static struct timeout_user *add_timeout_user(long long timeout, void (*callback)(void *), void *arg)
{ assert(timeout == master_socket_timeout && callback == server_shutdown_timeout && !arg); event('T'); return &timer; }
static void remove_timeout_user(struct timeout_user *value) { assert(value == &timer); event('X'); }
static void close_master_socket(long long timeout) { assert(!timeout); event('C'); }
static void shutdown_master_socket(void) { shutdown_calls++; }
static void flush_registry(void) { event('F'); }
static void set_gluon_user_active(int active) { event(active ? 'L' : 'U'); }
static void release_object(void *obj) { assert(obj); event('R'); }
static void grab_object(void *obj) { assert(obj); }
static void list_add_tail(int *list, int *entry) { assert(list && entry); }
'''

MAIN = r'''
static void reset(void)
{
    memset(events, 0, sizeof(events)); event_count = 0;
    gluon_session_mode = 1; user_processes = running_processes = 1;
    shutdown_stage = shutdown_calls = 0; shutdown_timeout = NULL; master_socket_timeout = -1200000000LL;
}
int main(void)
{
    struct process game = {1, 0, 0, 0, 0}, service = {2, 1, 0, 0, 0};
    struct thread thread = {0};
    reset(); process_died(&game); assert(!strcmp(events, "RFUT") && !user_processes && shutdown_timeout);
    reset(); user_processes = running_processes = 2; process_died(&game); assert(!strcmp(events, "R") && user_processes == 1);
    reset(); process_died(&service); assert(!strcmp(events, "R") && user_processes == 1);
    reset(); shutdown_stage = 1; process_died(&game); assert(!strcmp(events, "RFUC") && !shutdown_timeout);
    reset(); gluon_session_mode = 0; process_died(&game); assert(!strcmp(events, "RUT"));
    reset(); user_processes = running_processes = 0; shutdown_timeout = &timer;
    add_process_thread(&game, &thread); assert(!strcmp(events, "LX") && user_processes == 1 && !shutdown_timeout);
    add_process_thread(&game, &thread); assert(!strcmp(events, "LX") && user_processes == 1);
    reset(); user_processes = running_processes = 0; service.running_threads = 0;
    add_process_thread(&service, &thread); assert(!*events && !user_processes && running_processes == 1);
    reset(); assert(shutdown_gluon_idle_session() == 2 && !shutdown_calls);
    reset(); user_processes = 0; shutdown_stage = 1; assert(shutdown_gluon_idle_session() == 2 && !shutdown_calls);
    reset(); user_processes = 0; gluon_session_mode = 0; assert(shutdown_gluon_idle_session() == 2 && !shutdown_calls);
    reset(); user_processes = 0; assert(shutdown_gluon_idle_session() == 0 && shutdown_calls == 1);
    reset(); user_processes = running_processes = 0; init_gluon_session_timeout(); assert(!strcmp(events, "T"));
    init_gluon_session_timeout(); assert(!strcmp(events, "T"));
    reset(); init_gluon_session_timeout(); assert(!*events);
    reset(); user_processes = 0; gluon_session_mode = 0; init_gluon_session_timeout(); assert(!*events);
    puts("Wine warm-session lifecycle: 16 cases passed");
    return 0;
}
'''


def check(methods, *, must_pass=True):
    with tempfile.TemporaryDirectory(prefix='gluon-wine-session-') as work:
        source, binary = Path(work) / 'session.c', Path(work) / 'session'
        source.write_text(PREAMBLE + '\n' + methods + '\n' + MAIN)
        subprocess.run(['cc', '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', str(source), '-o', str(binary)], check=True)
        result = subprocess.run([str(binary)], capture_output=True, text=True)
        if (result.returncode == 0) != must_pass:
            raise RuntimeError('warm lifecycle behavioral control failed: ' + result.stdout + result.stderr)
        if must_pass:
            print(result.stdout.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--source-only', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    methods = extract(args.source)
    if args.source_only:
        print('Wine warm-session source boundaries passed')
        return
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Wine session fixtures require GitHub Actions')
    check(methods)
    if args.self_test:
        for before, after in (
            ('user_processes || shutdown_stage', '0 || shutdown_stage'),
            ('if (gluon_session_mode) flush_registry();', 'if (0) flush_registry();'),
            ('set_gluon_user_active( 0 );', 'set_gluon_user_active( 1 );'),
            ('set_gluon_user_active( 1 );', 'set_gluon_user_active( 0 );'),
            ('if (!user_processes++)', 'if (user_processes++)'),
            ('gluon_session_mode && !user_processes && !shutdown_timeout', '0 && !user_processes && !shutdown_timeout'),
        ):
            if before not in methods:
                raise ValueError('missing lifecycle negative-control target: ' + before)
            check(methods.replace(before, after, 1), must_pass=False)
        print('Wine warm-session lifecycle: 6 behavioral negative controls passed')


if __name__ == '__main__':
    main()
