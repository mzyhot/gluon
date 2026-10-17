/* Cloud-only fixture controller. Never built into the package. */
#import <AppKit/AppKit.h>
#import <errno.h>
#import <limits.h>
#import <stdlib.h>
#import <stdio.h>

int main(int argc, char **argv)
{
    if (argc != 2) return 64;
    char *end;
    errno = 0;
    long number = strtol(argv[1], &end, 10);
    if (errno || !*argv[1] || *end || number <= 0 || number > INT_MAX) return 64;
    @autoreleasepool
    {
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:3.0];
        do
        {
            NSRunningApplication *app = [NSRunningApplication runningApplicationWithProcessIdentifier:(pid_t)number];
            if (app && app.finishedLaunching)
            {
                if (app.activationPolicy != NSApplicationActivationPolicyAccessory) return 2;
                if (![app terminate]) return 3;
                puts("Cocoa terminate accepted for accessory application");
                return 0;
            }
            [NSThread sleepForTimeInterval:0.025];
        } while ([deadline timeIntervalSinceNow] > 0);
        fputs("Fixture host never registered with Launch Services\n", stderr);
        return 4;
    }
}
