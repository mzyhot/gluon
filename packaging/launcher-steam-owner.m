/* Cloud-only fake Steam application. Never shipped or run with Wine. */
#import <AppKit/AppKit.h>
#import <stdio.h>
#import <unistd.h>

@interface GluonFixtureSteam : NSObject <NSApplicationDelegate>
@property(nonatomic, copy) NSString *readyPath;
@end

@implementation GluonFixtureSteam
- (void)applicationDidFinishLaunching:(NSNotification *)notification
{
    (void)notification;
    FILE *ready = fopen(self.readyPath.fileSystemRepresentation, "w");
    if (!ready) _exit(70);
    fprintf(ready, "%d\n", getpid());
    if (fclose(ready)) _exit(70);
}

- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender
{
    (void)sender;
    return NSTerminateNow;
}
@end

int main(int argc, char **argv)
{
    if (argc != 2 || argv[1][0] != '/') return 64;
    @autoreleasepool
    {
        GluonFixtureSteam *delegate = [GluonFixtureSteam new];
        delegate.readyPath = [NSString stringWithUTF8String:argv[1]];
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
        NSApp.delegate = delegate;
        [NSApp run];
    }
    return 0;
}
