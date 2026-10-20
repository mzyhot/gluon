/* Actions-only external observer. No insertion, screen capture or game hooks. */
#import <AppKit/AppKit.h>
#import <CoreGraphics/CoreGraphics.h>
#include <mach/mach_time.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static uint64_t monotonic_nanoseconds(void)
{
    mach_timebase_info_data_t scale;
    (void)mach_timebase_info(&scale);
    return (uint64_t)((long double)mach_absolute_time() * scale.numer / scale.denom);
}

static NSString *canonical(NSString *path)
{
    return [[path stringByStandardizingPath] stringByResolvingSymlinksInPath];
}

int main(int argc, const char *argv[])
{
    const char *actions = getenv("GITHUB_ACTIONS");
    if (!actions || strcmp(actions, "true") || argc != 5) return 2;
    @autoreleasepool
    {
        NSString *bundle = canonical([NSString stringWithUTF8String:argv[1]]);
        NSString *executable = [bundle stringByAppendingPathComponent:@"Contents/MacOS/wine"];
        NSString *title = [NSString stringWithUTF8String:argv[2]];
        NSString *record = [NSString stringWithUTF8String:argv[3]];
        NSString *ready = [NSString stringWithUTF8String:argv[4]];
        /* Prime this external observer before the harness starts its clock. */
        (void)[NSWorkspace sharedWorkspace];
        CFArrayRef initial = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly |
                                                       kCGWindowListExcludeDesktopElements, kCGNullWindowID);
        if (!initial)
        {
            fprintf(stderr, "Electron observer has no GUI security session or WindowServer.\n");
            return 3;
        }
        CFRelease(initial);
        if (![@"ready\n" writeToFile:ready atomically:YES encoding:NSUTF8StringEncoding error:NULL]) return 4;
        uint64_t deadline = monotonic_nanoseconds() + 55ULL * NSEC_PER_SEC;
        NSArray *lastOwnedWindows = @[];
        while (monotonic_nanoseconds() < deadline)
        {
            @autoreleasepool
            {
                /* Metadata only. Names may be filtered without Screen Recording;
                   PID/layer/bounds identify the fixture without requesting it. */
                CFArrayRef information = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly |
                                                                  kCGWindowListExcludeDesktopElements,
                                                                  kCGNullWindowID);
                if (!information) return 5;
                NSDictionary *observed = nil;
                NSMutableArray *ownedWindows = [NSMutableArray array];
                for (NSDictionary *window in (__bridge NSArray *)information)
                {
                    pid_t pid = [window[(__bridge NSString *)kCGWindowOwnerPID] intValue];
                    NSRunningApplication *owner = [NSRunningApplication runningApplicationWithProcessIdentifier:pid];
                    if (!owner || ![canonical([[owner bundleURL] path]) isEqualToString:bundle] ||
                        ![canonical([[owner executableURL] path]) isEqualToString:executable]) continue;
                    CGRect bounds;
                    NSDictionary *coordinates = window[(__bridge NSString *)kCGWindowBounds];
                    if (!coordinates || !CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)coordinates, &bounds))
                        continue;
                    NSString *name = window[(__bridge NSString *)kCGWindowName];
                    int layer = [window[(__bridge NSString *)kCGWindowLayer] intValue];
                    double alpha = [window[(__bridge NSString *)kCGWindowAlpha] doubleValue];
                    if (ownedWindows.count < 8)
                        [ownedWindows addObject:@{@"pid": @(pid), @"layer": @(layer), @"alpha": @(alpha),
                            @"width": @(bounds.size.width), @"height": @(bounds.size.height),
                            @"title_available": @(name.length > 0), @"title_matches": @([name isEqualToString:title])}];
                    if (layer != 0 || alpha <= 0) continue;
                    /* This generated app has exactly one 640x480 normal window.
                       Exclude Wine's desktop, tray and helper placeholder windows. */
                    if (bounds.size.width < 608 || bounds.size.width > 672 ||
                        bounds.size.height < 432 || bounds.size.height > 544) continue;
                    if (name.length && ![name isEqualToString:title]) continue;
                    /* Capture after confirming identity: observation latency is
                       included, and the result is not a compositor timestamp. */
                    observed = @{@"monotonic_ns": @(monotonic_nanoseconds()), @"pid": @(pid),
                        @"window_number": window[(__bridge NSString *)kCGWindowNumber],
                        @"width": @(bounds.size.width), @"height": @(bounds.size.height),
                        @"bundle_path": bundle, @"executable_path": executable,
                        @"title_available": @(name.length > 0), @"poll_milliseconds": @20};
                    break;
                }
                if (ownedWindows.count) lastOwnedWindows = [ownedWindows copy];
                CFRelease(information);
                if (observed)
                {
                    NSData *data = [NSJSONSerialization dataWithJSONObject:observed options:0 error:NULL];
                    return data && [data writeToFile:record options:NSDataWritingAtomic error:NULL] ? 0 : 6;
                }
            }
            usleep(20000);
        }
        fprintf(stderr, "The generated Electron fixture did not expose its native window before the deadline.\n");
        NSData *diagnostic = [NSJSONSerialization dataWithJSONObject:lastOwnedWindows options:0 error:NULL];
        if (diagnostic) fprintf(stderr, "Last fixture-owned window metadata: %.*s\n",
                                (int)diagnostic.length, (const char *)diagnostic.bytes);
        return 7;
    }
}
