/* Actions-only startup ordering probe. Never packaged or loaded by Gluon. */
#import <AppKit/AppKit.h>
#import <objc/runtime.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

@interface NSApplication (GluonStartupFixture)
+ (NSApplication *)gluon_fixture_sharedApplication;
@end

@implementation NSApplication (GluonStartupFixture)
+ (void)load
{
    if (!getenv("GLUON_FIXTURE_CHILD_READY")) return;
    Method original = class_getClassMethod(self, @selector(sharedApplication));
    Method probe = class_getClassMethod(self, @selector(gluon_fixture_sharedApplication));
    method_exchangeImplementations(original, probe);
}

+ (NSApplication *)gluon_fixture_sharedApplication
{
    const char *ready = getenv("GLUON_FIXTURE_CHILD_READY");
    const char *record = getenv("GLUON_FIXTURE_COCOA_RESULT");
    /* A serialized launcher cannot produce its child's marker while AppKit is
     * waiting here. A parallel launcher can, without a product-side test hook. */
    for (int count = 0; ready && access(ready, F_OK) && count < 1000; count++) usleep(1000);
    if (record)
    {
        const char *result = ready && !access(ready, F_OK) ? "child-first\n" : "appkit-first\n";
        int fd = open(record, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
        if (fd >= 0) { (void)write(fd, result, strlen(result)); close(fd); }
    }
    return [self gluon_fixture_sharedApplication];
}
@end
