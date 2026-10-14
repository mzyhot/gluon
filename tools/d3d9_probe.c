/* Small D3D9 rendering check: clear, draw a fixed-function triangle, read pixels.
 * Build with llvm-mingw's {x86_64,i686}-w64-mingw32-clang.
 * Run in an isolated Wine prefix; no game installation or Steam is needed.
 * An optional DLL path allows comparison with Wine's saved .wined3d original.
 */
#define COBJMACROS
#include <windows.h>
#include <d3d9.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define CHECK(call) do { HRESULT result = (call); if (FAILED(result)) { \
    fprintf(stderr, "%s failed: %#lx\n", #call, (unsigned long)result); return 1; \
} } while (0)

int main(int argc, char **argv)
{
    typedef HRESULT (WINAPI *create9ex_fn)(UINT, IDirect3D9Ex **);
    const char *library = argc > 1 ? argv[1] : "d3d9.dll";
    HMODULE module = LoadLibraryA(library);
    if (!module) { fprintf(stderr, "LoadLibrary(%s) failed: %lu\n", library, GetLastError()); return 1; }
    create9ex_fn create9ex = (create9ex_fn)GetProcAddress(module, "Direct3DCreate9Ex");
    if (!create9ex) { fprintf(stderr, "Direct3DCreate9Ex export missing\n"); return 1; }
    IDirect3D9Ex *d3d = NULL;
    IDirect3DDevice9Ex *device = NULL;
    IDirect3DSurface9 *target = NULL, *readback = NULL;
    D3DADAPTER_IDENTIFIER9 adapter;
    D3DPRESENT_PARAMETERS present;
    D3DLOCKED_RECT locked;
    const uint32_t background = 0xff123456, triangle = 0xff3366aa;
    struct vertex { float x, y, z, rhw; uint32_t color; } vertices[] = {
        { 0, 0, 0.5f, 1, triangle },
        { 16, 0, 0.5f, 1, triangle },
        { 0, 16, 0.5f, 1, triangle },
    };
    HWND window = CreateWindowA("STATIC", "Gluon D3D9 probe", WS_OVERLAPPEDWINDOW,
        0, 0, 64, 64, NULL, NULL, GetModuleHandleA(NULL), NULL);
    if (!window) { fprintf(stderr, "CreateWindow failed: %lu\n", GetLastError()); return 1; }
    CHECK(create9ex(D3D_SDK_VERSION, &d3d));
    CHECK(IDirect3D9Ex_GetAdapterIdentifier(d3d, D3DADAPTER_DEFAULT, 0, &adapter));
    printf("D3D9 %u-bit adapter: %s\n", (unsigned)(8 * sizeof(void *)), adapter.Description);
    memset(&present, 0, sizeof(present));
    present.BackBufferWidth = 32;
    present.BackBufferHeight = 32;
    present.BackBufferFormat = D3DFMT_UNKNOWN;
    present.BackBufferCount = 1;
    present.SwapEffect = D3DSWAPEFFECT_DISCARD;
    present.hDeviceWindow = window;
    present.Windowed = TRUE;
    present.PresentationInterval = D3DPRESENT_INTERVAL_IMMEDIATE;
    CHECK(IDirect3D9Ex_CreateDeviceEx(d3d, D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, window,
        D3DCREATE_SOFTWARE_VERTEXPROCESSING, &present, NULL, &device));
    CHECK(IDirect3DDevice9Ex_CreateRenderTarget(device, 16, 16, D3DFMT_A8R8G8B8,
        D3DMULTISAMPLE_NONE, 0, FALSE, &target, NULL));
    CHECK(IDirect3DDevice9Ex_CreateOffscreenPlainSurface(device, 16, 16,
        D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &readback, NULL));
    CHECK(IDirect3DDevice9Ex_SetRenderTarget(device, 0, target));
    CHECK(IDirect3DDevice9Ex_SetRenderState(device, D3DRS_CULLMODE, D3DCULL_NONE));
    CHECK(IDirect3DDevice9Ex_SetRenderState(device, D3DRS_LIGHTING, FALSE));
    CHECK(IDirect3DDevice9Ex_SetRenderState(device, D3DRS_ZENABLE, FALSE));
    CHECK(IDirect3DDevice9Ex_SetTextureStageState(device, 0, D3DTSS_COLOROP, D3DTOP_SELECTARG1));
    CHECK(IDirect3DDevice9Ex_SetTextureStageState(device, 0, D3DTSS_COLORARG1, D3DTA_DIFFUSE));
    CHECK(IDirect3DDevice9Ex_SetFVF(device, D3DFVF_XYZRHW | D3DFVF_DIFFUSE));
    CHECK(IDirect3DDevice9Ex_Clear(device, 0, NULL, D3DCLEAR_TARGET, background, 1, 0));
    CHECK(IDirect3DDevice9Ex_BeginScene(device));
    CHECK(IDirect3DDevice9Ex_DrawPrimitiveUP(device, D3DPT_TRIANGLELIST, 1, vertices, sizeof(vertices[0])));
    CHECK(IDirect3DDevice9Ex_EndScene(device));
    CHECK(IDirect3DDevice9Ex_GetRenderTargetData(device, target, readback));
    CHECK(IDirect3DSurface9_LockRect(readback, &locked, NULL, D3DLOCK_READONLY));
    uint32_t corner = *(uint32_t *)((char *)locked.pBits + 15 * locked.Pitch + 15 * 4);
    uint32_t center = *(uint32_t *)((char *)locked.pBits + 3 * locked.Pitch + 3 * 4);
    CHECK(IDirect3DSurface9_UnlockRect(readback));
    printf("Clear/readback: %#x (expected %#x); triangle: %#x (expected %#x)\n",
        corner, background, center, triangle);
    IDirect3DSurface9_Release(readback);
    IDirect3DSurface9_Release(target);
    IDirect3DDevice9Ex_Release(device);
    IDirect3D9Ex_Release(d3d);
    DestroyWindow(window);
    FreeLibrary(module);
    if ((corner & 0xffffff) != (background & 0xffffff) ||
        (center & 0xffffff) != (triangle & 0xffffff)) return 1;
    puts("D3D9 render and readback passed");
    return 0;
}
