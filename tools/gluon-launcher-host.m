/* Receive macOS application quit requests for the Steam-tracked launcher PID.
 * Wine and all prefix cleanup remain in the shell child, which this host owns.
 */
#import <AppKit/AppKit.h>
#import <spawn.h>
#import <sys/wait.h>
#import <signal.h>
#import <errno.h>
#import <dlfcn.h>
#import <fcntl.h>
#import <sys/stat.h>
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

/* The client runs from <Steam root>/Steam.AppBundle/Steam/Contents/MacOS, and
 * Steam writes its console log under <Steam root>/logs. */
static NSString *steam_console_log(NSString *directory)
{
    NSArray<NSString *> *parts = directory.pathComponents;
    NSUInteger count = parts.count;
    if (count < 6 || ![parts[count - 1] isEqual:@"MacOS"] || ![parts[count - 2] isEqual:@"Contents"] ||
        ![parts[count - 3] isEqual:@"Steam"] || ![parts[count - 4] isEqual:@"Steam.AppBundle"]) return nil;
    NSString *root = [NSString pathWithComponents:[parts subarrayWithRange:NSMakeRange(0, count - 4)]];
    return [root stringByAppendingPathComponent:@"logs/console_log.txt"];
}

static BOOL line_contains(NSData *line, const char *text)
{
    return memmem(line.bytes, line.length, text, strlen(text)) != NULL;
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
@property(nonatomic) int steamLog;
@property(nonatomic) off_t steamLogOffset;
@property(nonatomic, strong) NSMutableData *steamLogLine;
@property(nonatomic) double steamExitDeadline;
@property(nonatomic) double raiseDeadline;
- (void)poll;
@end

@implementation GluonLauncherHost
- (instancetype)init
{
    if ((self = [super init])) _steamLog = -1;
    return self;
}

- (void)finish:(int)status
{
    if (self.finished) return;
    self.finished = YES;
    self.result = status;
    [self.timer invalidate];
    self.timer = nil;
    if (self.steamLog >= 0) { close(self.steamLog); self.steamLog = -1; }
    self.steamLogLine = nil;
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

/* Steam logs an exit request as soon as the user quits it, then waits for
 * running games before it exits or signals them. Only lines written after this
 * host started count. */
- (void)watchSteamConsole:(NSString *)path
{
    if (!path) return;
    int log = open(path.fileSystemRepresentation, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (log < 0) return;
    struct stat info;
    if (fstat(log, &info) || !S_ISREG(info.st_mode))
    {
        close(log);
        return;
    }
    self.steamLog = log;
    self.steamLogOffset = info.st_size;
    self.steamLogLine = [NSMutableData data];
    fprintf(stderr, "gluon: Steam's exit request will stop this session\n");
}

- (void)steamConsoleLine:(NSData *)line
{
    /* Steam refuses an exit during its own startup and says so right after. */
    if (line_contains(line, "ExecuteSteamURL exit : not allowed yet"))
    {
        if (self.steamExitDeadline)
            fprintf(stderr, "gluon: Steam refused its exit request; session continues\n");
        self.steamExitDeadline = 0;
    }
    else if (line_contains(line, "ExecuteSteamURL: \"") && line_contains(line, "steam://exit"))
        self.steamExitDeadline = NSProcessInfo.processInfo.systemUptime + 1.0;
}

- (void)readSteamConsole
{
    struct stat info;
    if (self.steamLog < 0 || fstat(self.steamLog, &info)) return;
    if (info.st_size < self.steamLogOffset)
    {
        self.steamLogOffset = 0;
        self.steamLogLine.length = 0;
    }
    char buffer[16384];
    /* A large burst is finished on the following polls. */
    for (int reads = 0; reads < 8 && self.steamLogOffset < info.st_size; reads++)
    {
        ssize_t count = pread(self.steamLog, buffer, sizeof(buffer), self.steamLogOffset);
        if (count <= 0) break;
        self.steamLogOffset += count;
        const char *start = buffer, *end = buffer + count;
        while (start < end)
        {
            const char *newline = memchr(start, '\n', (size_t)(end - start));
            const char *stop = newline ? newline : end;
            if (self.steamLogLine.length < 65536)
                [self.steamLogLine appendBytes:start length:(NSUInteger)(stop - start)];
            if (!newline) break;
            [self steamConsoleLine:self.steamLogLine];
            self.steamLogLine.length = 0;
            start = newline + 1;
        }
    }
}

- (void)poll
{
    if (self.finished || [self reap]) return;
    if (self.raiseDeadline && !self.stopping)
    {
        if (NSProcessInfo.processInfo.systemUptime >= self.raiseDeadline)
        {
            fprintf(stderr, "gluon: the game's window did not appear within 60 seconds of Steam's raise\n");
            self.raiseDeadline = 0;
        }
        else if (NSApp.active && [self handActivationToGame]) self.raiseDeadline = 0;
    }
    if (!self.stopping)
    {
        [self readSteamConsole];
        if (self.steamExitDeadline && NSProcessInfo.processInfo.systemUptime >= self.steamExitDeadline)
        {
            fprintf(stderr, "gluon: Steam is exiting; stopping session\n");
            [self requestStop:SIGTERM];
        }
    }
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
            /* SteamAPI_Init obtains SteamUtils for its pipe; SteamShutdown_t is
             * a SteamUtils callback. Without it, the Steam exit fallback remains. */
            if (!methods->getUtils || !methods->getUtils(client, steamPipe, GLUON_STEAM_UTILS_VERSION))
                fprintf(stderr, "gluon: %s unavailable on the callback pipe; Steam exit still stops this "
                        "session\n", GLUON_STEAM_UTILS_VERSION);
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

- (BOOL)handActivationToGame
{
    const char *value = getenv("STEAM_COMPAT_APP_ID");
    if (!value || !*value) value = getenv("SteamAppId");
    NSString *appid = value && *value ? [NSString stringWithUTF8String:value] : nil;
    if (!appid) return NO;
    NSString *identifier = [@"org.gluon.game." stringByAppendingString:appid];
    for (NSRunningApplication *app in [NSRunningApplication
        runningApplicationsWithBundleIdentifier:identifier])
    {
        /* Only the game's window process is a regular app; Wine's helpers are not. */
        if (app.terminated || app.activationPolicy != NSApplicationActivationPolicyRegular) continue;
        if (@available(macOS 14.0, *))
        {
            [NSApp yieldActivationToApplication:app];
            [app activateFromApplication:NSRunningApplication.currentApplication options:0];
        }
        else [app activateWithOptions:0];
        fprintf(stderr, "gluon: passing Steam's activation to the game's app\n");
        return YES;
    }
    return NO;
}

/* Steam raises a running game through the process it tracks, which is this
 * host. Big Picture does so as soon as the game is launched, before the game
 * has a window, and keeps its own full-screen Space in front. While this host
 * stays active, the activation is passed to the game's app once its window
 * process exists; macOS then shows the Space with the game's window. */
- (void)applicationDidBecomeActive:(NSNotification *)notification
{
    (void)notification;
    if (self.finished) return;
    fprintf(stderr, "gluon: Steam activated the game's launcher host\n");
    self.raiseDeadline = [self handActivationToGame] ? 0 : NSProcessInfo.processInfo.systemUptime + 60.0;
}

- (void)applicationDidResignActive:(NSNotification *)notification
{
    (void)notification;
    if (self.raiseDeadline)
        fprintf(stderr, "gluon: launcher host lost activation before the game's window appeared\n");
    self.raiseDeadline = 0;
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
        [host watchSteamConsole:steam_console_log(host.steamDirectory)];
        if (host.steamOwner)
            fprintf(stderr, "gluon: Steam application %d exit will stop this session\n",
                host.steamOwner.processIdentifier);
        else if (host.steamDirectory)
            fprintf(stderr, "gluon: owning Steam application not identified; exit fallback inactive\n");
        NSApp.delegate = host;
        __weak GluonLauncherHost *weakHost = host;
        host.timer = [NSTimer timerWithTimeInterval:0.1 repeats:YES block:^(NSTimer *timer) {
            (void)timer;
            [weakHost poll];
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
