/* Native CGL capability probe. This does not change Wine or launch a game. */
#define GL_SILENCE_DEPRECATION
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>

static int has_extension(const char *wanted, GLint major)
{
    if (major >= 3) {
        GLint count = 0;
        glGetIntegerv(GL_NUM_EXTENSIONS, &count);
        for (GLint i = 0; i < count; i++) {
            const char *extension = (const char *)glGetStringi(GL_EXTENSIONS, i);
            if (extension && !strcmp(extension, wanted)) return 1;
        }
    } else {
        const char *extensions = (const char *)glGetString(GL_EXTENSIONS);
        size_t length = strlen(wanted);
        const char *found = extensions;
        while (found && (found = strstr(found, wanted))) {
            if ((found == extensions || found[-1] == ' ') &&
                (found[length] == ' ' || found[length] == '\0')) return 1;
            found += length;
        }
    }
    return 0;
}

static void probe_integer_attribute(GLint major)
{
    typedef void (*integer_pointer)(GLuint, GLint, GLenum, GLsizei, const void *);
    const char *symbols[] = {"glVertexAttribIPointer", "glVertexAttribIPointerEXT"};
    GLuint vao = 0, buffer = 0;
    int extension = has_extension("GL_EXT_gpu_shader4", major);

    printf("  GL_EXT_gpu_shader4=%s\n", extension ? "advertised" : "absent");
    if (major < 3 && !extension) return;
    if (major >= 3) {
        glGenVertexArrays(1, &vao);
        glBindVertexArray(vao);
    }
    glGenBuffers(1, &buffer);
    glBindBuffer(GL_ARRAY_BUFFER, buffer);
    for (unsigned i = 0; i < sizeof(symbols) / sizeof(symbols[0]); i++) {
        integer_pointer function = (integer_pointer)dlsym(RTLD_DEFAULT, symbols[i]);
        GLint integer = 0;
        GLenum call_error, query_error;
        if (!function) {
            printf("  %s=absent\n", symbols[i]);
            continue;
        }
        /* Only evaluate integer input when the context advertises that feature. */
        glVertexAttribPointer(0, 1, GL_INT, GL_FALSE, sizeof(GLint), NULL);
        GLenum setup_error = glGetError();
        function(0, 1, GL_INT, sizeof(GLint), NULL);
        call_error = glGetError();
        glGetVertexAttribiv(0, GL_VERTEX_ATTRIB_ARRAY_INTEGER, &integer);
        query_error = glGetError();
        printf("  %s=present setup_error=0x%x call_error=0x%x integer_state=%d query_error=0x%x\n",
               symbols[i], setup_error, call_error, integer, query_error);
    }
    glDeleteBuffers(1, &buffer);
    if (vao) glDeleteVertexArrays(1, &vao);
}

static int probe(const char *name, CGLPixelFormatAttribute profile)
{
    CGLPixelFormatAttribute attributes[] = {
        kCGLPFAAccelerated, kCGLPFAOpenGLProfile, profile, 0
    };
    CGLPixelFormatObj format = NULL;
    CGLContextObj context = NULL;
    GLint count = 0, major = 0, minor = 0, flags = 0, mask = 0;
    GLint compiled = GL_FALSE;
    GLfloat line_range[2] = {0, 0}, line_width = 0;
    GLenum line_error;
    CGLError result;
    GLuint shader;
    int failed = 0;
    const char *source =
        "#version 330 core\n"
        "void main() { gl_Position = vec4(0.0, 0.0, 0.0, 1.0); }\n";

    printf("context=%s\n", name);
    result = CGLChoosePixelFormat(attributes, &format, &count);
    if (result != kCGLNoError || !format) {
        printf("  CGLChoosePixelFormat: %s\n", CGLErrorString(result));
        return 1;
    }
    result = CGLCreateContext(format, NULL, &context);
    CGLReleasePixelFormat(format);
    if (result != kCGLNoError || !context) {
        printf("  CGLCreateContext: %s\n", CGLErrorString(result));
        return 1;
    }
    result = CGLSetCurrentContext(context);
    if (result != kCGLNoError) {
        printf("  CGLSetCurrentContext: %s\n", CGLErrorString(result));
        CGLReleaseContext(context);
        return 1;
    }

    const char *version = (const char *)glGetString(GL_VERSION);
    printf("  GL_VERSION=%s\n", version ? version : "(null)");
    printf("  GL_RENDERER=%s\n", glGetString(GL_RENDERER));
    printf("  GL_SHADING_LANGUAGE_VERSION=%s\n",
           glGetString(GL_SHADING_LANGUAGE_VERSION));
    if (!version || sscanf(version, "%d.%d", &major, &minor) != 2) {
        failed = 1;
    } else if (major >= 3) {
        glGetIntegerv(GL_CONTEXT_FLAGS, &flags);
        glGetIntegerv(GL_CONTEXT_PROFILE_MASK, &mask);
        printf("  GL_CONTEXT_FLAGS=0x%x GL_CONTEXT_PROFILE_MASK=0x%x\n", flags, mask);
    }
    printf("  host_glVertexAttribIPointer_symbol=%s\n",
           dlsym(RTLD_DEFAULT, "glVertexAttribIPointer") ? "present" : "absent");
    probe_integer_attribute(major);

    /* A global framework export says nothing about support in this context. */
    shader = glCreateShader(GL_VERTEX_SHADER);
    if (shader) {
        glShaderSource(shader, 1, &source, NULL);
        glCompileShader(shader);
        glGetShaderiv(shader, GL_COMPILE_STATUS, &compiled);
        printf("  GLSL_330_vertex_shader=%s\n", compiled ? "accepted" : "rejected");
        if (!compiled) {
            char message[512] = {0};
            glGetShaderInfoLog(shader, sizeof(message), NULL, message);
            printf("  shader_log=%s", message);
            if (!strchr(message, '\n')) putchar('\n');
        }
        glDeleteShader(shader);
    } else {
        printf("  GLSL_330_vertex_shader=unavailable\n");
        failed = 1;
    }
    /* Forward compatibility removes wide lines even from the core profile. */
    glGetFloatv(GL_ALIASED_LINE_WIDTH_RANGE, line_range);
    GLenum previous_error = glGetError();
    if (previous_error != GL_NO_ERROR)
        printf("  setup_GL_error=0x%x\n", previous_error);
    glLineWidth(2.0f);
    line_error = glGetError();
    glGetFloatv(GL_LINE_WIDTH, &line_width);
    printf("  line_width_range=%.1f..%.1f line_width_2_error=0x%x resulting_width=%.1f\n",
           line_range[0], line_range[1], line_error, line_width);

    if (profile != (CGLPixelFormatAttribute)kCGLOGLPVersion_Legacy &&
        (major < 3 || !compiled || !(mask & GL_CONTEXT_CORE_PROFILE_BIT)))
        failed = 1;
    CGLSetCurrentContext(NULL);
    CGLReleaseContext(context);
    return failed;
}

int main(void)
{
    int failed = 0;
#if defined(__aarch64__)
    puts("architecture=arm64");
#elif defined(__x86_64__)
    puts("architecture=x86_64");
#else
    puts("architecture=unknown");
#endif
    failed |= probe("legacy", (CGLPixelFormatAttribute)kCGLOGLPVersion_Legacy);
    failed |= probe("3.2-core", (CGLPixelFormatAttribute)kCGLOGLPVersion_3_2_Core);
    return failed;
}
