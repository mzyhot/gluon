/* Receive macOS application quit requests for the Steam-tracked launcher PID.
 * Wine and all prefix cleanup remain in the shell child, which this host owns.
 */
#import <AppKit/AppKit.h>
#import <spawn.h>
#import <sys/wait.h>
#import <signal.h>
#import <errno.h>
#import <stdio.h>
#import <stdlib.h>
#import <stdlib.h>
#import <string.h>
#import <unistd.h>

extern char **environ;
static volatile sig_atomic_t received_signal;

static void remember_signal(int number)
{
    if (!received_signal) received_signal = number;
}

@interface GluonLauncherHost : NSObject <NSApplicationDelegate>
@property(nonatomic) char **arguments;
@property(nonatomic) pid_t child;
@property(nonatomic) BOOL stopping;
@property(nonatomic) BOOL finished;
@property(nonatomic) BOOL forced;
@property(nonatomic) int result;
@property(nonatomic) double stopDeadline;
@property(nonatomic) double reapDeadline;
@property(nonatomic, strong) NSTimer *timer;
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
        NSApp.delegate = host;
        host.timer = [NSTimer timerWithTimeInterval:0.1 repeats:YES block:^(NSTimer *timer) {
            (void)timer;
            [host poll];
        }];
        [NSRunLoop.mainRunLoop addTimer:host.timer forMode:NSRunLoopCommonModes];
        [NSApp run];
        return host.result;
    }
}
