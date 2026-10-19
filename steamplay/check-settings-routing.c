// Included after the exact production FindToolForTargetApp hook, extracted by
// check-settings.py. These fixtures preserve actual policy/control-flow order.
int main(void) {
    orig_FindToolForTargetApp = (void *)fixture_original;
    platforms = COMPAT_PLATFORM_WINDOWS | COMPAT_PLATFORM_MACOS;
    auto_mac32 = 1;
    assert(hook_FindToolForTargetApp((void *)1, 123) == &primary);
    assert(cloud_checks == 1);
    assert(hook_FindToolForTargetApp((void *)1, 456) == NULL); // Unknown/Mac64.
    mapping = "other-tool"; // An unavailable explicit tool must not become Gluon.
    assert(hook_FindToolForTargetApp((void *)1, 123) == NULL);
    mapping = "gluon.none";
    assert(hook_FindToolForTargetApp((void *)1, 123) == NULL);
    mapping = NULL;
    auto_mac32 = 0;
    assert(hook_FindToolForTargetApp((void *)1, 123) == NULL);
    platforms = COMPAT_PLATFORM_MACOS;
    auto_mac32 = 1;
    assert(hook_FindToolForTargetApp((void *)1, 123) == NULL); // Mac-only has no Windows transport.
    platforms = COMPAT_PLATFORM_WINDOWS;
    assert(hook_FindToolForTargetApp((void *)1, 456) == &primary); // Existing Windows default.
    original_tool = &other;
    assert(hook_FindToolForTargetApp((void *)1, 123) == &other);
    original_tool = &primary;
    assert(hook_FindToolForTargetApp((void *)1, 123) == &primary);
    assert(hook_FindToolForTargetApp((void *)1, 0) == &primary);
    puts("Native global Mac32 routing, explicit choices and Cloud preflight fixture passed");
    return 0;
}
