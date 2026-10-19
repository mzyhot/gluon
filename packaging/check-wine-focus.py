#!/usr/bin/env python3
"""Exercise Wine's exact bundle-focus methods against a virtual macOS clock.

The methods come from the shipping patch (or an explicitly supplied patched
source file). Foundation-only fakes replace AppKit and dispatch; the fixture
never opens an application, window, Steam client or Wine process. Execution and
compilation require a GitHub Actions Mac. --source-only only reads source.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


CASES = (
    "visible-late", "visible-after-old-timeout", "hidden-timeout",
    "failed-activation", "late-failed-activation", "active-current-space", "active-other-space",
    "foreign-window", "minimized-window", "focus-disabled", "non-bundle",
    "repeat-start", "user-switched", "switch-during-open",
    "game-child-frontmost", "owner-child-frontmost",
)


def extract_methods(source):
    start = source.index("    - (BOOL) gluonWindowShown:")
    end = source.index("    - (void) tryToActivateIgnoringOtherApps:", start)
    methods = source[start:end]
    for required in (
        "- (BOOL) gluonFocusStillOwnedBy:",
        "- (void) bringBundleForward:",
        "- (void) bringBundleForwardOnce",
        "attempt >= 3", "systemUptime >= deadline", 'getenv("GLUON_FOCUS")',
    ):
        if required not in methods:
            raise ValueError("focus methods missing source boundary: " + required)
    return methods


def patched_method_source(patch):
    section = patch.split("+++ b/dlls/winemac.drv/cocoa_app.m\n", 1)[1]
    return "\n".join(line[1:] for line in section.splitlines()
                     if line.startswith(("+", " ")) and not line.startswith("+++"))


PREAMBLE = r'''
#import <Foundation/Foundation.h>
#include <dispatch/dispatch.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static NSTimeInterval fixture_now;
static NSUInteger requests, fallbacks, polls;
static NSTimeInterval first_request = -1;
static BOOL activation_succeeds = YES;
static NSTimeInterval completion_delay = 0.025;
static NSURL *bundle_url;

@interface FixtureTask : NSObject
@property NSTimeInterval when;
@property(copy) dispatch_block_t block;
@end
@implementation FixtureTask
@end
static NSMutableArray<FixtureTask *> *tasks;
static void schedule(NSTimeInterval when, dispatch_block_t block)
{
    FixtureTask *task = [FixtureTask new];
    task.when = when;
    task.block = block;
    [tasks addObject:task];
}
static dispatch_time_t fixture_dispatch_time(dispatch_time_t base, int64_t delay)
{
    (void)base;
    return (dispatch_time_t)(fixture_now * NSEC_PER_SEC) + delay;
}
static void fixture_dispatch_after(dispatch_time_t when, dispatch_queue_t queue, dispatch_block_t block)
{
    (void)queue;
    schedule((NSTimeInterval)when / NSEC_PER_SEC, block);
}
static void fixture_dispatch_async(dispatch_queue_t queue, dispatch_block_t block)
{
    (void)queue;
    schedule(fixture_now, block);
}
static dispatch_queue_t fixture_dispatch_get_main_queue(void) { return NULL; }

@interface FixtureWindow : NSObject
@property(getter=isVisible) BOOL visible;
@property(getter=isMiniaturized) BOOL miniaturized;
@property(getter=isOnActiveSpace) BOOL onActiveSpace;
@end
@implementation FixtureWindow
@end
@interface WineWindow : FixtureWindow
@end
@implementation WineWindow
@end
@interface FixtureApplication : NSObject
@property(getter=isActive) BOOL active;
@property(copy) NSArray *windows;
@end
@implementation FixtureApplication
@end
static FixtureApplication *NSApp;
static WineWindow *game_window;

@interface FixtureRunningApplication : NSObject
@property pid_t processIdentifier;
@property(copy) NSString *bundleIdentifier;
+ (instancetype)currentApplication;
@end
static FixtureRunningApplication *current_app, *front_app;
@implementation FixtureRunningApplication
+ (instancetype)currentApplication { return current_app; }
@end
@interface FixtureBundle : NSObject
+ (instancetype)mainBundle;
- (NSURL *)bundleURL;
- (NSString *)bundleIdentifier;
@end
@implementation FixtureBundle
+ (instancetype)mainBundle { static FixtureBundle *instance; if (!instance) instance = [self new]; return instance; }
- (NSURL *)bundleURL { return bundle_url; }
- (NSString *)bundleIdentifier { return current_app.bundleIdentifier; }
@end
@interface FixtureProcessInfo : NSObject
+ (instancetype)processInfo;
- (NSTimeInterval)systemUptime;
@end
@implementation FixtureProcessInfo
+ (instancetype)processInfo { static FixtureProcessInfo *instance; if (!instance) instance = [self new]; return instance; }
- (NSTimeInterval)systemUptime { return fixture_now; }
@end
@interface FixtureOpenConfiguration : NSObject
@property BOOL activates;
@property BOOL createsNewApplicationInstance;
@property BOOL addsToRecentItems;
+ (instancetype)configuration;
@end
@implementation FixtureOpenConfiguration
+ (instancetype)configuration { return [self new]; }
@end
@interface FixtureWorkspace : NSObject
+ (instancetype)sharedWorkspace;
- (FixtureRunningApplication *)frontmostApplication;
- (void)openApplicationAtURL:(NSURL *)url configuration:(FixtureOpenConfiguration *)configuration
          completionHandler:(void (^)(FixtureRunningApplication *, NSError *))completion;
@end
@implementation FixtureWorkspace
+ (instancetype)sharedWorkspace { static FixtureWorkspace *instance; if (!instance) instance = [self new]; return instance; }
- (FixtureRunningApplication *)frontmostApplication { return front_app; }
- (void)openApplicationAtURL:(NSURL *)url configuration:(FixtureOpenConfiguration *)configuration
          completionHandler:(void (^)(FixtureRunningApplication *, NSError *))completion
{
    if (![url isEqual:bundle_url] || !configuration.activates || configuration.createsNewApplicationInstance ||
        configuration.addsToRecentItems || !game_window.isVisible || game_window.isMiniaturized)
    {
        fprintf(stderr, "unsafe activation request\n");
        exit(2);
    }
    if (!requests++) first_request = fixture_now;
    schedule(fixture_now + completion_delay, ^{
        if (activation_succeeds)
        {
            NSApp.active = YES;
            game_window.onActiveSpace = YES;
            front_app = current_app;
        }
        completion(current_app, nil);
    });
}
@end

#define NSWindow FixtureWindow
#define NSRunningApplication FixtureRunningApplication
#define NSBundle FixtureBundle
#define NSProcessInfo FixtureProcessInfo
#define NSWorkspace FixtureWorkspace
#define NSWorkspaceOpenConfiguration FixtureOpenConfiguration
#define dispatch_time fixture_dispatch_time
#define dispatch_after fixture_dispatch_after
#define dispatch_async fixture_dispatch_async
#define dispatch_get_main_queue fixture_dispatch_get_main_queue

@interface FixtureController : NSObject
- (void)bringBundleForwardOnce;
- (void)tryToActivateIgnoringOtherApps:(BOOL)ignore;
@end
@implementation FixtureController
- (void)tryToActivateIgnoringOtherApps:(BOOL)ignore
{
    (void)ignore;
    fallbacks++;
    [self bringBundleForwardOnce];
}
'''

EPILOGUE = r'''
@end
#define EXPECT(test, message) do { if (!(test)) { fprintf(stderr, "%s at %.3f (%lu requests, %lu fallbacks)\n", message, fixture_now, (unsigned long)requests, (unsigned long)fallbacks); return 1; } } while (0)
static FixtureRunningApplication *application(pid_t pid, NSString *identifier)
{
    FixtureRunningApplication *result = [FixtureRunningApplication new];
    result.processIdentifier = pid;
    result.bundleIdentifier = identifier;
    return result;
}
int main(int argc, const char **argv)
{
    @autoreleasepool {
        if (argc != 2) return 2;
        NSString *scenario = [NSString stringWithUTF8String:argv[1]];
        tasks = [NSMutableArray new];
        NSApp = [FixtureApplication new];
        current_app = application(200, @"org.gluon.game.fixture");
        front_app = application(100, @"com.valvesoftware.steam");
        bundle_url = [NSURL fileURLWithPath:@"/tmp/Game.app"];
        game_window = [WineWindow new];
        NSApp.windows = @[game_window];
        FixtureController *controller = [FixtureController new];
        NSTimeInterval visible_at = 0;
        BOOL late_visibility = NO, no_request = NO;
        if ([scenario isEqualToString:@"visible-late"] || [scenario isEqualToString:@"visible-after-old-timeout"] ||
            [scenario isEqualToString:@"late-failed-activation"])
        {
            visible_at = [scenario isEqualToString:@"visible-late"] ? 1.51 :
                ([scenario isEqualToString:@"late-failed-activation"] ? 9.75 : 5.01);
            late_visibility = YES;
            schedule(visible_at, ^{ game_window.visible = YES; });
        }
        else if ([scenario isEqualToString:@"hidden-timeout"])
            no_request = YES;
        else if ([scenario isEqualToString:@"foreign-window"])
        {
            FixtureWindow *other = [FixtureWindow new];
            other.visible = YES;
            other.onActiveSpace = YES;
            NSApp.windows = @[game_window, other];
            no_request = YES;
        }
        else
            game_window.visible = YES;
        if ([scenario isEqualToString:@"failed-activation"] || [scenario isEqualToString:@"late-failed-activation"])
            activation_succeeds = NO;
        if ([scenario isEqualToString:@"active-current-space"] || [scenario isEqualToString:@"active-other-space"])
        {
            NSApp.active = YES;
            game_window.onActiveSpace = [scenario isEqualToString:@"active-current-space"];
            no_request = game_window.onActiveSpace;
        }
        if ([scenario isEqualToString:@"minimized-window"])
        {
            game_window.miniaturized = YES;
            no_request = YES;
        }
        if ([scenario isEqualToString:@"focus-disabled"])
        {
            setenv("GLUON_FOCUS", "0", 1);
            no_request = YES;
        }
        if ([scenario isEqualToString:@"non-bundle"])
        {
            bundle_url = [NSURL fileURLWithPath:@"/tmp/wine"];
            no_request = YES;
        }
        if ([scenario isEqualToString:@"user-switched"] || [scenario isEqualToString:@"game-child-frontmost"] ||
            [scenario isEqualToString:@"owner-child-frontmost"])
        {
            game_window.visible = NO;
            visible_at = 0.31;
            late_visibility = YES;
            no_request = [scenario isEqualToString:@"user-switched"];
            NSString *identifier = no_request ? @"com.example.editor" :
                ([scenario isEqualToString:@"game-child-frontmost"] ? current_app.bundleIdentifier : front_app.bundleIdentifier);
            FixtureRunningApplication *next = application(201, identifier);
            schedule(0.15, ^{ front_app = next; });
            schedule(visible_at, ^{ game_window.visible = YES; });
        }
        if ([scenario isEqualToString:@"switch-during-open"])
        {
            activation_succeeds = NO;
            completion_delay = 0.25;
            FixtureRunningApplication *other = application(300, @"com.example.editor");
            schedule(0.10, ^{ front_app = other; });
        }
        [controller bringBundleForwardOnce];
        if ([scenario isEqualToString:@"repeat-start"])
        {
            for (unsigned i = 0; i < 10; i++) [controller bringBundleForwardOnce];
            EXPECT(tasks.count == 1, "more than one readiness loop");
        }
        if ([scenario isEqualToString:@"focus-disabled"] || [scenario isEqualToString:@"non-bundle"])
            EXPECT(tasks.count == 0, "disabled focus scheduled work");
        while (tasks.count)
        {
            EXPECT(polls++ < 1000, "unbounded readiness loop");
            NSUInteger next = 0;
            for (NSUInteger i = 1; i < tasks.count; i++)
                if (tasks[i].when < tasks[next].when) next = i;
            FixtureTask *task = tasks[next];
            [tasks removeObjectAtIndex:next];
            /* Main-queue callbacks advance time even for a zero-delay block. */
            fixture_now = task.when > fixture_now ? task.when : fixture_now + 0.000001;
            EXPECT(fixture_now <= 10.051, "readiness exceeded deadline");
            task.block();
        }
        if (no_request)
            EXPECT(requests == 0 && fallbacks == 0, "unwanted focus activation");
        else if ([scenario isEqualToString:@"failed-activation"])
            EXPECT(requests == 3 && fallbacks == 3, "visible activation attempts not bounded at three");
        else if ([scenario isEqualToString:@"late-failed-activation"])
            EXPECT(requests == 1 && fallbacks == 1, "visible retry ignored readiness deadline");
        else if ([scenario isEqualToString:@"switch-during-open"])
            EXPECT(requests == 1 && fallbacks == 0, "focus stolen after user switched during open");
        else
        {
            EXPECT(requests == 1, "visible handoff did not finish promptly");
            EXPECT(fallbacks == 0, "unnecessary activation after success");
            if (late_visibility)
                EXPECT(first_request >= visible_at && first_request <= visible_at + 0.051, "visible window waited more than one readiness poll");
            else
                EXPECT(first_request <= 0.051, "ready window handoff delayed");
        }
        if ([scenario isEqualToString:@"hidden-timeout"] || [scenario isEqualToString:@"minimized-window"] ||
            [scenario isEqualToString:@"foreign-window"])
            EXPECT(fixture_now >= 9.99 && polls <= 202, "hidden readiness did not stop at its deadline");
    }
    return 0;
}
'''


def compile_fixture(methods, directory, name):
    source = directory / (name + ".m")
    binary = directory / name
    source.write_text(PREAMBLE + methods + EPILOGUE)
    subprocess.run(["clang", "-fobjc-arc", "-fblocks", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-parameter", "-framework", "Foundation", str(source), "-o", str(binary)],
                   check=True, capture_output=True, text=True, timeout=30)
    return binary


def run_case(binary, case):
    env = {key: value for key, value in os.environ.items() if key != "GLUON_FOCUS"}
    return subprocess.run([str(binary), case], env=env, capture_output=True, text=True, timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wine-source", type=Path)
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.wine_source:
        source = (args.wine_source / "dlls/winemac.drv/cocoa_app.m").read_text()
    else:
        patch = Path(__file__).resolve().parent / "patches/wine-macos-app-bundle.patch"
        source = patched_method_source(patch.read_text())
    methods = extract_methods(source)
    if args.source_only:
        print("Wine focus methods extracted; compilation and execution not requested")
        return 0
    if sys.platform != "darwin" or os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("focus fixture compilation/execution requires a GitHub Actions Mac")
    with tempfile.TemporaryDirectory(prefix="gluon-wine-focus-") as temp:
        directory = Path(temp)
        binary = compile_fixture(methods, directory, "focus")
        for case in CASES:
            result = run_case(binary, case)
            if result.returncode:
                raise ValueError("focus fixture failed " + case + ":\n" + result.stderr)
        if args.self_test:
            mutations = (
                ("hidden-consumes-attempt", "attempt:attempt deadline:deadline focusOwner:owner delay:0.05",
                 "attempt:attempt + 1 deadline:deadline focusOwner:owner delay:0.05", "visible-late"),
                ("slow-readiness", "focusOwner:owner delay:0.05", "focusOwner:owner delay:2.5", "visible-late"),
                ("too-many-attempts", "attempt >= 3", "attempt >= 300", "failed-activation"),
                ("no-deadline", ">= deadline", "< -1", "hidden-timeout"),
                ("focus-stolen", "![self gluonFocusStillOwnedBy:owner]", "NO", "user-switched"),
                ("ignored-opt-out", '!strcmp(focus, "0")', '!strcmp(focus, "off")', "focus-disabled"),
                ("wrong-space", "if ([NSApp isActive] && onActiveSpace) return;",
                 "if ([NSApp isActive]) return;", "active-other-space"),
            )
            for name, before, after, case in mutations:
                changed = methods.replace(before, after)
                if changed == methods:
                    raise ValueError("focus mutation did not change source: " + name)
                binary = compile_fixture(changed, directory, name)
                result = run_case(binary, case)
                if result.returncode != 1:
                    raise ValueError("focus fixture missed negative control " + name + ":\n" + result.stderr)
    print("Wine focus passed %d virtual-clock cases%s" %
          (len(CASES), " and 7 behavioral negative controls" if args.self_test else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as error:
        print("Wine focus check: " + (error.stderr or str(error)), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError) as error:
        print("Wine focus check: " + str(error), file=sys.stderr)
        sys.exit(1)
