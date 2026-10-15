/* Receive macOS application quit requests for the Steam-tracked launcher PID.
 * Wine and all prefix cleanup remain in the shell child, which this host owns.
 */
#import <AppKit/AppKit.h>
#import <spawn.h>
#import <sys/wait.h>
#import <signal.h>
#import <errno.h>
#import <dlfcn.h>
#import <stdio.h>
#import <stdlib.h>
#import <string.h>
#import <unistd.h>
#import "gluon-steam-client.h"

extern char **environ;
static volatile sig_atomic_t received_signal;

static void remember_signal(int number)
{
    if (!received_signal) received_signal = number;
}

static NSString *steam_client_directory(void)
{
    const char *launched = getenv("SteamClientLaunch");
    if (!launched || strcmp(launched, "1")) return nil;
    const char *config = getenv("STEAM_CLIENT_CONFIG_FILE");
    const char *compat = getenv("STEAM_COMPAT_CLIENT_INSTALL_PATH");
    NSString *directory = nil;
    if (config && *config)
        directory = [[NSString stringWithUTF8String:config] stringByDeletingLastPathComponent];
    else if (compat && *compat) directory = [NSString stringWithUTF8String:compat];
    if (!directory.isAbsolutePath) return nil;
    return directory.stringByStandardizingPath.stringByResolvingSymlinksInPath;
}

static NSString *steam_executable(NSString *bundle)
{
    if (!bundle.isAbsolutePath) return nil;
    NSDictionary *info = [NSDictionary dictionaryWithContentsOfFile:
        [bundle stringByAppendingPathComponent:@"Contents/Info.plist"]];
    if (![info[@"CFBundleIdentifier"] isEqual:@"com.valvesoftware.steam"] ||
        ![info[@"CFBundleExecutable"] isEqual:@"steam_osx"] ||
        ![info[@"CFBundlePackageType"] isEqual:@"APPL"]) return nil;
    return [bundle stringByAppendingPathComponent:@"Contents/MacOS/steam_osx"]
        .stringByStandardizingPath.stringByResolvingSymlinksInPath;
}

static NSRunningApplication *steam_owner(NSString *directory)
{
    if (!directory) return nil;
    NSMutableSet<NSString *> *executables = [NSMutableSet set];
    NSString *bundled = steam_executable(directory.stringByDeletingLastPathComponent
        .stringByDeletingLastPathComponent);
    if (bundled) [executables addObject:bundled];
    const char *installed = getenv("STEAM_APP_BUNDLE_PATH");
    NSString *bootstrap = installed ? steam_executable([NSString stringWithUTF8String:installed]) : nil;
    if (bootstrap) [executables addObject:bootstrap];
    NSRunningApplication *owner = nil;
    for (NSRunningApplication *app in [NSRunningApplication
        runningApplicationsWithBundleIdentifier:@"com.valvesoftware.steam"])
    {
        NSString *path = app.executableURL.path.stringByStandardizingPath.stringByResolvingSymlinksInPath;
        if (!app.terminated && path && [executables containsObject:path])
        {
            if (owner) return nil; /* Never guess between client instances. */
            owner = app;
        }
    }
    return owner;
}

@interface GluonLauncherHost : NSObject <NSApplicationDelegate>
@property(nonatomic) char **arguments;
@property(nonatomic) pid_t child;
@property(nonatomic) BOOL stopping;
@property(atomic) BOOL finished;
@property(nonatomic) BOOL forced;
@property(nonatomic) int result;
@property(nonatomic) double stopDeadline;
@property(nonatomic) double reapDeadline;
@property(nonatomic, strong) NSTimer *timer;
@property(nonatomic, strong) NSString *steamDirectory;
@property(nonatomic, strong) NSRunningApplication *steamOwner;
@property(nonatomic, strong) dispatch_semaphore_t steamWatcherDone;
- (void)poll;
@end

@implementation GluonLauncherHost
- (void)finish:(int)status
{
    if (self.finished) return;
    self.finished = YES;
    self.result = status;
    [self.timer invalidate];
    [NSApp stop:nil];
    /* Wake run's nextEvent wait when finish was called by a timer/delegate. */
    [NSApp postEvent:[NSEvent otherEventWithType:NSEventTypeApplicationDefined
        location:NSZeroPoint modifierFlags:0 timestamp:0 windowNumber:0
        context:nil subtype:0 data1:0 data2:0] atStart:YES];
}

- (BOOL)reap
{
    if (!self.child) return NO;
    int status;
    pid_t result = waitpid(self.child, &status, WNOHANG);
    if (!result || (result < 0 && errno == EINTR)) return NO;
    /* This is the only waitpid caller. Until reap, an exited child retains its
     * PID, so a concurrent exit cannot redirect a signal to a reused PID. */
    self.child = 0;
    if (result < 0)
    {
        fprintf(stderr, "gluon: launcher host lost child ownership: %s\n", strerror(errno));
        [self finish:125];
    }
    else if (self.forced) [self finish:124];
    else if (WIFEXITED(status)) [self finish:WEXITSTATUS(status)];
    else if (WIFSIGNALED(status)) [self finish:128 + WTERMSIG(status)];
    else [self finish:125];
    return YES;
}

- (void)requestStop:(int)number
{
    if (self.finished || self.stopping) return;
    self.stopping = YES;
    if (!self.child)
    {
        [self finish:128 + number];
        return;
    }
    if ([self reap]) return;
    const char *name = number == SIGINT ? "INT" : number == SIGHUP ? "HUP" : "TERM";
    fprintf(stderr, "gluon: launcher host forwarding %s to child %d\n", name, self.child);
    self.stopDeadline = NSProcessInfo.processInfo.systemUptime + 20.0;
    if (kill(self.child, number) && errno != ESRCH)
        fprintf(stderr, "gluon: launcher host could not signal child: %s\n", strerror(errno));
}

- (void)poll
{
    if (self.finished || [self reap]) return;
    if (self.steamOwner.terminated && !self.stopping)
    {
        fprintf(stderr, "gluon: owning Steam application exited; stopping session\n");
        [self requestStop:SIGTERM];
    }
    if (received_signal && !self.stopping) [self requestStop:(int)received_signal];
    if (!self.child || !self.stopping || self.finished) return;
    double now = NSProcessInfo.processInfo.systemUptime;
    if (!self.forced && now >= self.stopDeadline)
    {
        /* The shell's own prefix-specific cleanup has a shorter deadline. Do
         * not turn a cleanup failure into a claim that the prefix is stopped. */
        fprintf(stderr, "gluon: launcher cleanup exceeded 20 seconds; killing owned shell child only\n");
        if ([self reap]) return;
        self.forced = YES;
        self.reapDeadline = now + 5.0;
        kill(self.child, SIGKILL);
    }
    else if (self.forced && now >= self.reapDeadline)
    {
        fprintf(stderr, "gluon: launcher child did not reap after forced cleanup; prefix shutdown unconfirmed\n");
        [self finish:124];
    }
}

/* Runs on the watcher thread only. Steam sends SteamShutdown_t to connected
 * clients when the user quits it, then waits for their games to exit; a game
 * that ignores the callback holds Steam open. */
- (void)runSteamWatcher:(NSString *)directory
{
    NSString *path = [directory stringByAppendingPathComponent:@"steamclient.dylib"];
    void *library = dlopen(path.fileSystemRepresentation, RTLD_NOW | RTLD_LOCAL);
    if (!library) fprintf(stderr, "gluon: Steam client library: %s\n", dlerror());
    GluonSteamCreateInterface create = library ?
        (GluonSteamCreateInterface)dlsym(library, "CreateInterface") : NULL;
    GluonSteamGetCallback get = library ?
        (GluonSteamGetCallback)dlsym(library, "Steam_BGetCallback") : NULL;
    GluonSteamFreeCallback freeCallback = library ?
        (GluonSteamFreeCallback)dlsym(library, "Steam_FreeLastCallback") : NULL;
    GluonSteamReleaseThreadMemory releaseThreadMemory = library ?
        (GluonSteamReleaseThreadMemory)dlsym(library, "Steam_ReleaseThreadLocalMemory") : NULL;
    if (!create || !get || !freeCallback)
    {
        fprintf(stderr, "gluon: Steam shutdown callback unavailable; Cocoa Stop remains active\n");
        return;
    }
    GluonSteamClient *client = create("SteamClient020", NULL);
    const GluonSteamClientVTable *methods = client ? client->vtable : NULL;
    if (!methods || !methods->createPipe || !methods->connectUser ||
        !methods->releaseUser || !methods->releasePipe)
        fprintf(stderr, "gluon: SteamClient020 shutdown interface unavailable\n");
    else
    {
        int steamPipe = methods->createPipe(client);
        int user = steamPipe ? methods->connectUser(client, steamPipe) : 0;
        if (user)
        {
            fprintf(stderr, "gluon: watching Steam shutdown on owned callback pipe\n");
            while (!self.finished)
            {
                BOOL shutdown = NO;
                /* Drain only this host's private pipe. The game's callbacks
                 * stay on its own pipe, even when it ignores SteamShutdown. */
                for (int count = 0; count < 32 && !self.finished; count++)
                {
                    GluonSteamCallback callback = {0};
                    int call = 0;
                    if (!get(steamPipe, &callback, &call)) break;
                    shutdown = callback.identifier == GLUON_STEAM_SHUTDOWN;
                    freeCallback(steamPipe);
                    if (shutdown) break;
                }
                if (shutdown)
                {
                    dispatch_async(dispatch_get_main_queue(), ^{
                        fprintf(stderr, "gluon: Steam shutdown requested; stopping session\n");
                        [self requestStop:SIGTERM];
                    });
                    break;
                }
                [NSThread sleepForTimeInterval:0.1];
            }
            methods->releaseUser(client, steamPipe, user);
        }
        else fprintf(stderr, "gluon: Steam shutdown callback pipe could not connect\n");
        if (steamPipe) methods->releasePipe(client, steamPipe);
    }
    if (releaseThreadMemory) releaseThreadMemory(true);
    /* Steam owns worker threads in this image. Do not dlclose while they may
     * still be running, or join IPC from the UI thread. */
}

- (void)watchSteamShutdown
{
    NSString *directory = self.steamDirectory;
    if (!directory) return;
    /* Steam IPC or library initialization must never block Cocoa Stop, shell
     * reaping or the cleanup deadline. Only the main thread signals the child. */
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    self.steamWatcherDone = done;
    [NSThread detachNewThreadWithBlock:^{
        @autoreleasepool
        {
            [self runSteamWatcher:directory];
        }
        dispatch_semaphore_signal(done);
    }];
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification
{
    (void)notification;
    if (self.finished || self.stopping) return;
    if (received_signal)
    {
        [self requestStop:(int)received_signal];
        return;
    }
    posix_spawnattr_t attributes;
    int error = posix_spawnattr_init(&attributes);
    if (error)
    {
        fprintf(stderr, "gluon: launcher host spawn attributes: %s\n", strerror(error));
        [self finish:126];
        return;
    }
    sigset_t defaults, mask;
    sigemptyset(&defaults);
    sigemptyset(&mask);
    for (int number = 1; number < NSIG; number++)
        if (number != SIGKILL && number != SIGSTOP) sigaddset(&defaults, number);
    error = posix_spawnattr_setsigdefault(&attributes, &defaults);
    if (!error) error = posix_spawnattr_setsigmask(&attributes, &mask);
    if (!error) error = posix_spawnattr_setflags(&attributes,
        POSIX_SPAWN_SETSIGDEF | POSIX_SPAWN_SETSIGMASK);
    if (!error) error = posix_spawn(&_child, "/bin/bash", NULL, &attributes, self.arguments, environ);
    posix_spawnattr_destroy(&attributes);
    if (error)
    {
        fprintf(stderr, "gluon: launcher host could not start launcher: %s\n", strerror(error));
        [self finish:126];
        return;
    }
    /* Cocoa callbacks and the timer are serialized on the main thread. A
     * POSIX signal during spawn only sets a flag, checked after PID capture. */
    if (received_signal) [self requestStop:(int)received_signal];
    [self watchSteamShutdown];
}

- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender
{
    (void)sender;
    fprintf(stderr, "gluon: launcher host Cocoa quit received\n");
    [self requestStop:SIGTERM];
    return NSTerminateCancel;
}
@end

int main(int argc, char **argv)
{
    if (argc < 2 || argv[1][0] != '/' || access(argv[1], R_OK))
    {
        fprintf(stderr, "Usage: gluon-launcher-host /absolute/path/to/gluon [arguments...]\n");
        return 64;
    }
    /* Steam's insertion belongs only on the final Wine launch. The shell
     * restores a validated overlay list there; this native host loads none. */
    unsetenv("DYLD_INSERT_LIBRARIES");
    if (setenv("GLUON_LAUNCHER_HOST", "1", 1)) return 126;

    struct sigaction action = {0};
    action.sa_handler = remember_signal;
    sigemptyset(&action.sa_mask);
    for (int number = 1; number < NSIG; number++)
    {
        if (number != SIGTERM && number != SIGINT && number != SIGHUP) continue;
        if (sigaction(number, &action, NULL)) return 126;
    }
    /* An ignored SIGCHLD can auto-reap children and destroy PID ownership. */
    action.sa_handler = SIG_DFL;
    if (sigaction(SIGCHLD, &action, NULL)) return 126;
    sigset_t mask;
    sigemptyset(&mask);
    if (sigprocmask(SIG_SETMASK, &mask, NULL)) return 126;

    @autoreleasepool
    {
        GluonLauncherHost *host = [GluonLauncherHost new];
        /* Reuse the original argv allocation without copying argument bytes. */
        argv[0] = "/bin/bash";
        host.arguments = argv;
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
        host.steamDirectory = steam_client_directory();
        host.steamOwner = steam_owner(host.steamDirectory);
        if (host.steamOwner)
            fprintf(stderr, "gluon: Steam application %d exit will stop this session\n",
                host.steamOwner.processIdentifier);
        else if (host.steamDirectory)
            fprintf(stderr, "gluon: owning Steam application not identified; exit fallback inactive\n");
        NSApp.delegate = host;
        host.timer = [NSTimer timerWithTimeInterval:0.1 repeats:YES block:^(NSTimer *timer) {
            (void)timer;
            [host poll];
        }];
        [NSRunLoop.mainRunLoop addTimer:host.timer forMode:NSRunLoopCommonModes];
        [NSApp run];
        /* Let the watcher release its Steam pipe, but never wait on blocked IPC. */
        if (host.steamWatcherDone)
            dispatch_semaphore_wait(host.steamWatcherDone,
                dispatch_time(DISPATCH_TIME_NOW, (int64_t)NSEC_PER_SEC));
        /* steamclient.dylib may still run its own threads, so skip image
         * destructors that could race them. The child is already reaped. */
        fflush(NULL);
        _exit(host.result);
    }
}
