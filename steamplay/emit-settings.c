// Emit the actual compiled bootstrap and getter, so C preprocessing (including
// trigraph handling) cannot silently corrupt JavaScript that header decoding misses.
#include "../build/notproton/dylib/feats/webpatch.c"
#include <assert.h>
#include <stdio.h>
int np_log_level = -1;
FILE *np_log_file;
char *np_nativeplatform_javascript(void) { return NULL; }
int main(int argc, char **argv) {
    assert(argc == 3);
    size_t length = 0;
    const char *input = g_fixes[0].find;
    char *output = np_webpatch_transform((const uint8_t *)input, strlen(input), &length, NULL);
    const char *suffix = g_fixes[0].replace;
    size_t suffix_length = strlen(suffix);
    assert(output && length >= suffix_length);
    assert(!strcmp(output + length - suffix_length, suffix));
    FILE *bootstrap = fopen(argv[1], "w"), *getter = fopen(argv[2], "w");
    assert(bootstrap && getter);
    size_t bootstrap_length = length - suffix_length;
    assert(fwrite(output, 1, bootstrap_length, bootstrap) == bootstrap_length);
    assert(fprintf(getter, "({%s})", GLUON_INVALID_OS_GETTER) > 0);
    assert(!fclose(bootstrap) && !fclose(getter));
    free(output);
    return 0;
}
