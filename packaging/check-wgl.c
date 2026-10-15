/* Source-built Windows probe for Wine's desktop OpenGL dispatch. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <GL/gl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define GL_VERTEX_ATTRIB_ARRAY_INTEGER 0x88fd
#define GL_FRAMEBUFFER_EXT 0x8d40
#define GL_FRAMEBUFFER_BINDING_EXT 0x8ca6
#define GL_COLOR_ATTACHMENT0_EXT 0x8ce0
#define GL_FRAMEBUFFER_COMPLETE_EXT 0x8cd5
#define WGL_CONTEXT_MAJOR_VERSION_ARB 0x2091
#define WGL_CONTEXT_MINOR_VERSION_ARB 0x2092
#define WGL_CONTEXT_FLAGS_ARB 0x2094
#define WGL_CONTEXT_PROFILE_MASK_ARB 0x9126
#define WGL_CONTEXT_FORWARD_COMPATIBLE_BIT_ARB 0x0002
#define WGL_CONTEXT_CORE_PROFILE_BIT_ARB 0x0001
typedef void (APIENTRY *attrib_pointer_fn)(GLuint, GLint, GLenum, GLsizei, const void *);
typedef void (APIENTRY *attrib_integer_fn)(GLuint, GLenum, GLint *);
typedef void (APIENTRY *draw_arrays_instanced_fn)(GLenum, GLint, GLsizei, GLsizei);
typedef void (APIENTRY *draw_elements_instanced_fn)(GLenum, GLsizei, GLenum, const void *, GLsizei);
typedef void (APIENTRY *gen_framebuffers_fn)(GLsizei, GLuint *);
typedef void (APIENTRY *delete_framebuffers_fn)(GLsizei, const GLuint *);
typedef void (APIENTRY *bind_framebuffer_fn)(GLenum, GLuint);
typedef void (APIENTRY *framebuffer_texture_fn)(GLenum, GLenum, GLenum, GLuint, GLint);
typedef GLenum (APIENTRY *check_framebuffer_fn)(GLenum);
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

static int probe_instancing(const char *extensions, int major, int minor,
                            int require_alias, const char *phase)
{
    static const char *array_names[] = {
        "glDrawArraysInstanced", "glDrawArraysInstancedARB", "glDrawArraysInstancedEXT"
    };
    static const char *element_names[] = {
        "glDrawElementsInstanced", "glDrawElementsInstancedARB", "glDrawElementsInstancedEXT"
    };
    /* Both instances cover the same eight-pixel square. Additive blending makes
     * a two-instance draw distinguishable from a single draw or a no-op, without
     * requiring shader-language extensions in the legacy context. */
    static const GLfloat vertices[] = {-1, -1, 3, -1, -1, 3};
    static const GLubyte indices[] = {0, 1, 2};
    draw_arrays_instanced_fn arrays[3];
    draw_elements_instanced_fn elements[3];
    int native_core = major > 3 || (major == 3 && minor >= 1);
    int arb = has_extension(extensions, "GL_ARB_draw_instanced");
    int ext = has_extension(extensions, "GL_EXT_draw_instanced");
    int supported[] = {native_core || arb || ext, arb, ext};
    int failed = 0;
    unsigned int i;
    GLuint framebuffer = 0, texture = 0;
    GLint old_framebuffer = 0, old_texture = 0, old_matrix_mode = 0;
    GLenum status, error;
    gen_framebuffers_fn gen_framebuffers;
    delete_framebuffers_fn delete_framebuffers;
    bind_framebuffer_fn bind_framebuffer;
    framebuffer_texture_fn framebuffer_texture;
    check_framebuffer_fn check_framebuffer;

    printf("%sinstancing_GL_3_1=%s GL_ARB_draw_instanced=%s GL_EXT_draw_instanced=%s\n",
           phase, native_core ? "yes" : "no", arb ? "yes" : "no", ext ? "yes" : "no");
    for (i = 0; i < 3; i++) {
        arrays[i] = (draw_arrays_instanced_fn)(uintptr_t)wglGetProcAddress(array_names[i]);
        elements[i] = (draw_elements_instanced_fn)(uintptr_t)wglGetProcAddress(element_names[i]);
        printf("%s%s=%s %s=%s\n", phase,
               array_names[i], arrays[i] ? "present" : "absent",
               element_names[i], elements[i] ? "present" : "absent");
        if (supported[i]) {
            if ((i || native_core || require_alias) && (!arrays[i] || !elements[i])) failed = 1;
        } else if (arrays[i] || elements[i]) {
            fprintf(stderr, "%sInstancing entry point exposed without its capability.\n", phase);
            failed = 1;
        }
    }
    if (!supported[0]) {
        fprintf(stderr, "%sNo draw-instanced capability; readback cannot validate aliases.\n", phase);
        return failed || require_alias;
    }
    /* An FBO avoids depending on visibility or backing-store allocation of the
     * tiny probe window. Never call extension exports without its capability. */
    if (!has_extension(extensions, "GL_EXT_framebuffer_object")) {
        fprintf(stderr, "%sNo GL_EXT_framebuffer_object; readback prerequisite unavailable.\n", phase);
        return failed || require_alias;
    }
    gen_framebuffers = (gen_framebuffers_fn)(uintptr_t)wglGetProcAddress("glGenFramebuffersEXT");
    delete_framebuffers = (delete_framebuffers_fn)(uintptr_t)wglGetProcAddress("glDeleteFramebuffersEXT");
    bind_framebuffer = (bind_framebuffer_fn)(uintptr_t)wglGetProcAddress("glBindFramebufferEXT");
    framebuffer_texture = (framebuffer_texture_fn)(uintptr_t)wglGetProcAddress("glFramebufferTexture2DEXT");
    check_framebuffer = (check_framebuffer_fn)(uintptr_t)wglGetProcAddress("glCheckFramebufferStatusEXT");
    if (!gen_framebuffers || !delete_framebuffers || !bind_framebuffer ||
        !framebuffer_texture || !check_framebuffer) return 1;

    glGetIntegerv(GL_FRAMEBUFFER_BINDING_EXT, &old_framebuffer);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &old_texture);
    glGetIntegerv(GL_MATRIX_MODE, &old_matrix_mode);
    glPushAttrib(GL_ALL_ATTRIB_BITS);
    glPushClientAttrib(GL_CLIENT_ALL_ATTRIB_BITS);
    gen_framebuffers(1, &framebuffer);
    bind_framebuffer(GL_FRAMEBUFFER_EXT, framebuffer);
    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 8, 8, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    framebuffer_texture(GL_FRAMEBUFFER_EXT, GL_COLOR_ATTACHMENT0_EXT, GL_TEXTURE_2D, texture, 0);
    status = check_framebuffer(GL_FRAMEBUFFER_EXT);
    printf("%sinstancing_framebuffer_status=0x%x\n", phase, status);
    if (status != GL_FRAMEBUFFER_COMPLETE_EXT) {
        failed = 1;
        goto restore;
    }
    glMatrixMode(GL_PROJECTION);
    glPushMatrix();
    glLoadIdentity();
    glMatrixMode(GL_MODELVIEW);
    glPushMatrix();
    glLoadIdentity();
    glViewport(0, 0, 8, 8);
    glDrawBuffer(GL_COLOR_ATTACHMENT0_EXT);
    glReadBuffer(GL_COLOR_ATTACHMENT0_EXT);
    glDisable(GL_DITHER);
    glDisable(GL_LIGHTING);
    glDisable(GL_TEXTURE_2D);
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_CULL_FACE);
    glDisable(GL_ALPHA_TEST);
    glDisable(GL_SCISSOR_TEST);
    glEnable(GL_BLEND);
    glBlendFunc(GL_ONE, GL_ONE);
    glClearColor(0, 0, 0, 0);
    glColor4ub(0, 64, 0, 0);
    glEnableClientState(GL_VERTEX_ARRAY);
    glVertexPointer(2, GL_FLOAT, 0, vertices);
    error = glGetError();
    printf("%sinstancing_setup_GL_error=0x%x\n", phase, error);
    if (error != GL_NO_ERROR) failed = 1;
    for (i = 0; i < 3; i++) {
        GLubyte pixel[4] = {0, 0, 0, 0};
        if (!supported[i]) continue;
        if (arrays[i]) {
            glClear(GL_COLOR_BUFFER_BIT);
            arrays[i](GL_TRIANGLES, 0, 3, 2);
            glReadPixels(4, 4, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, pixel);
            error = glGetError();
            printf("%s%s_green=%u GL_error=0x%x\n", phase, array_names[i], (unsigned int)pixel[1], error);
            if (pixel[0] > 3 || pixel[1] < 125 || pixel[1] > 131 || pixel[2] > 3 ||
                error != GL_NO_ERROR) failed = 1;
        }
        if (elements[i]) {
            glClear(GL_COLOR_BUFFER_BIT);
            elements[i](GL_TRIANGLES, 3, GL_UNSIGNED_BYTE, indices, 2);
            glReadPixels(4, 4, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, pixel);
            error = glGetError();
            printf("%s%s_green=%u GL_error=0x%x\n", phase, element_names[i], (unsigned int)pixel[1], error);
            if (pixel[0] > 3 || pixel[1] < 125 || pixel[1] > 131 || pixel[2] > 3 ||
                error != GL_NO_ERROR) failed = 1;
        }
    }
    glMatrixMode(GL_MODELVIEW);
    glPopMatrix();
    glMatrixMode(GL_PROJECTION);
    glPopMatrix();
    glMatrixMode(old_matrix_mode);
restore:
    bind_framebuffer(GL_FRAMEBUFFER_EXT, old_framebuffer);
    glBindTexture(GL_TEXTURE_2D, old_texture);
    if (texture) glDeleteTextures(1, &texture);
    if (framebuffer) delete_framebuffers(1, &framebuffer);
    glPopClientAttrib();
    glPopAttrib();
    error = glGetError();
    printf("%sinstancing_restore_GL_error=0x%x\n", phase, error);
    return failed || error != GL_NO_ERROR;
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

    failed |= probe_instancing(extensions, major, minor, require_alias, "");

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
        PROC core_arrays = wglGetProcAddress("glDrawArraysInstanced");
        PROC core_elements = wglGetProcAddress("glDrawElementsInstanced");
        printf("core_glGetStringi=%s core_glVertexAttribIPointer=%s\n",
               core_stringi ? "present" : "absent", core_pointer ? "present" : "absent");
        if (require_alias && (!core_stringi || !core_pointer)) failed = 1;
        printf("core_glDrawArraysInstanced=%s core_glDrawElementsInstanced=%s\n",
               core_arrays ? "present" : "absent", core_elements ? "present" : "absent");
        if (require_alias && (!core_arrays || !core_elements)) failed = 1;
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
            failed |= probe_instancing(extensions, major, minor, require_alias, "after_switch_");
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
