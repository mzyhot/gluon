/* Source-built Windows probe for Wine's desktop OpenGL dispatch. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <GL/gl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define GL_VERTEX_ATTRIB_ARRAY_INTEGER 0x88fd
#define WGL_CONTEXT_MAJOR_VERSION_ARB 0x2091
#define WGL_CONTEXT_MINOR_VERSION_ARB 0x2092
#define WGL_CONTEXT_FLAGS_ARB 0x2094
#define WGL_CONTEXT_PROFILE_MASK_ARB 0x9126
#define WGL_CONTEXT_FORWARD_COMPATIBLE_BIT_ARB 0x0002
#define WGL_CONTEXT_CORE_PROFILE_BIT_ARB 0x0001
typedef void (APIENTRY *attrib_pointer_fn)(GLuint, GLint, GLenum, GLsizei, const void *);
typedef void (APIENTRY *attrib_integer_fn)(GLuint, GLenum, GLint *);
typedef HGLRC (WINAPI *create_context_fn)(HDC, HGLRC, const int *);

static int has_extension(const char *list, const char *name)
{
    size_t length = strlen(name);
    const char *match;
    if (!list) return 0;
    while ((match = strstr(list, name))) {
        if ((match == list || match[-1] == ' ') &&
            (match[length] == 0 || match[length] == ' ')) return 1;
        list = match + length;
    }
    return 0;
}

int main(int argc, char **argv)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    /* Static linking imports opengl32, so reaching main verifies its Unix bridge
     * loaded without requiring a window, CGL context, or GPU. */
    if (argc == 2 && !strcmp(argv[1], "--load-only")) {
        puts("Wine opengl32 module and Unix bridge loaded");
        return 0;
    }
    WNDCLASSA klass = {0};
    PIXELFORMATDESCRIPTOR format = {0};
    HWND window;
    HDC dc;
    HGLRC context, core_context;
    const char *extensions;
    char legacy_version[256];
    int major = 0, minor = 0;
    GLint integer = 0;
    GLint data[4] = {1, 2, 3, 4};
    GLenum error;
    int failed = 0;
    int require_alias = argc == 2 && !strcmp(argv[1], "--require-alias");
    attrib_pointer_fn core, ext;
    attrib_integer_fn get_attrib;
    create_context_fn create_context;

    if (argc > 2 || (argc == 2 && !require_alias)) {
        fprintf(stderr, "Usage: %s [--require-alias]\n", argv[0]);
        return 2;
    }
    klass.lpfnWndProc = DefWindowProcA;
    klass.hInstance = GetModuleHandleA(NULL);
    klass.lpszClassName = "Gluon OpenGL probe";
    if (!RegisterClassA(&klass)) return 1;
    window = CreateWindowA(klass.lpszClassName, "Gluon OpenGL probe", WS_OVERLAPPEDWINDOW,
                          0, 0, 64, 64, NULL, NULL, klass.hInstance, NULL);
    if (!window) return 1;
    dc = GetDC(window);
    format.nSize = sizeof(format);
    format.nVersion = 1;
    format.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER;
    format.iPixelType = PFD_TYPE_RGBA;
    format.cColorBits = 24;
    if (!SetPixelFormat(dc, ChoosePixelFormat(dc, &format), &format)) return 1;
    context = wglCreateContext(dc);
    if (!context || !wglMakeCurrent(dc, context)) return 1;

    snprintf(legacy_version, sizeof(legacy_version), "%s", glGetString(GL_VERSION));
    printf("GL_VERSION=%s\n", legacy_version);
    if (sscanf(legacy_version, "%d.%d", &major, &minor) != 2) failed = 1;
    extensions = (const char *)glGetString(GL_EXTENSIONS);
    printf("GL_EXT_gpu_shader4=%s\n", has_extension(extensions, "GL_EXT_gpu_shader4") ? "yes" : "no");
    core = (attrib_pointer_fn)(uintptr_t)wglGetProcAddress("glVertexAttribIPointer");
    ext = (attrib_pointer_fn)(uintptr_t)wglGetProcAddress("glVertexAttribIPointerEXT");
    get_attrib = (attrib_integer_fn)(uintptr_t)wglGetProcAddress("glGetVertexAttribiv");
    printf("glVertexAttribIPointer=%s glVertexAttribIPointerEXT=%s\n",
           core ? "present" : "absent", ext ? "present" : "absent");
    if (has_extension(extensions, "GL_EXT_gpu_shader4")) {
        if (!ext || !get_attrib || (require_alias && !core)) failed = 1;
        else {
            /* Integer-array state proves dispatch preserves integer semantics. */
            (core ? core : ext)(0, 4, GL_INT, 0, data);
            get_attrib(0, GL_VERTEX_ATTRIB_ARRAY_INTEGER, &integer);
            error = glGetError();
            printf("integer_array=%d GL_error=0x%x\n", integer, error);
            if (!integer || error != GL_NO_ERROR) failed = 1;
        }
    } else if (require_alias) {
        fprintf(stderr, "No GL_EXT_gpu_shader4; this host cannot validate the fallback.\n");
        failed = 1;
    }

    /* A pointer export must not grant features absent from this context. */
    PROC compute = wglGetProcAddress("glDispatchCompute");
    int compute_supported = major > 4 || (major == 4 && minor >= 3) ||
                            has_extension(extensions, "GL_ARB_compute_shader");
    printf("glDispatchCompute=%s advertised_compute=%s\n",
           compute ? "present" : "absent", compute_supported ? "yes" : "no");
    if (require_alias && !compute_supported && compute) failed = 1;

    create_context = (create_context_fn)(uintptr_t)wglGetProcAddress("wglCreateContextAttribsARB");
    core_context = NULL;
    if (create_context) {
        const int attributes[] = {
            WGL_CONTEXT_MAJOR_VERSION_ARB, 3,
            WGL_CONTEXT_MINOR_VERSION_ARB, 3,
            WGL_CONTEXT_PROFILE_MASK_ARB, WGL_CONTEXT_CORE_PROFILE_BIT_ARB,
            WGL_CONTEXT_FLAGS_ARB, WGL_CONTEXT_FORWARD_COMPATIBLE_BIT_ARB,
            0
        };
        core_context = create_context(dc, NULL, attributes);
    }
    if (core_context && wglMakeCurrent(dc, core_context)) {
        printf("core_GL_VERSION=%s\n", glGetString(GL_VERSION));
        PROC core_stringi = wglGetProcAddress("glGetStringi");
        PROC core_pointer = wglGetProcAddress("glVertexAttribIPointer");
        printf("core_glGetStringi=%s core_glVertexAttribIPointer=%s\n",
               core_stringi ? "present" : "absent", core_pointer ? "present" : "absent");
        if (require_alias && (!core_stringi || !core_pointer)) failed = 1;
        if (!wglMakeCurrent(dc, context)) failed = 1;
        else {
            const char *after = (const char *)glGetString(GL_VERSION);
            printf("returned_legacy_GL_VERSION=%s\n", after);
            if (strcmp(legacy_version, after)) failed = 1;
            PROC legacy_stringi = wglGetProcAddress("glGetStringi");
            core = (attrib_pointer_fn)(uintptr_t)wglGetProcAddress("glVertexAttribIPointer");
            get_attrib = (attrib_integer_fn)(uintptr_t)wglGetProcAddress("glGetVertexAttribiv");
            printf("after_switch_glGetStringi=%s glVertexAttribIPointer=%s\n",
                   legacy_stringi ? "present" : "absent", core ? "present" : "absent");
            if (require_alias && major < 3 && legacy_stringi) failed = 1;
            if (has_extension(extensions, "GL_EXT_gpu_shader4") && core && get_attrib) {
                integer = 0;
                core(0, 4, GL_INT, 0, data);
                get_attrib(0, GL_VERTEX_ATTRIB_ARRAY_INTEGER, &integer);
                error = glGetError();
                printf("after_switch_integer_array=%d GL_error=0x%x\n", integer, error);
                if (require_alias && (!integer || error != GL_NO_ERROR)) failed = 1;
            } else if (require_alias) failed = 1;
        }
    } else {
        printf("core_context=unavailable error=%lu\n", GetLastError());
        if (require_alias) failed = 1;
    }
    if (core_context) wglDeleteContext(core_context);
    wglMakeCurrent(NULL, NULL);
    wglDeleteContext(context);
    ReleaseDC(window, dc);
    DestroyWindow(window);
    UnregisterClassA(klass.lpszClassName, klass.hInstance);
    return failed;
}
