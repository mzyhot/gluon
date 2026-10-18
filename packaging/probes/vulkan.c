/* Gluon Vulkan packaging probe. Uses the published Wine Vulkan declarations.
 * Native: vulkan-probe /absolute/path/libMoltenVK.dylib [--device]
 * Windows: vulkan-probe.exe [--device]
 * The default checks library loading and instance extensions without needing a GPU.
 */
#ifdef WINE_UNIX_LIB
#include <windef.h>
#include <winbase.h>
#include <dlfcn.h>
#else
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif
#include <wine/vulkan.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int has_extension(const VkExtensionProperties *properties, uint32_t count, const char *name)
{
    for (uint32_t i = 0; i < count; ++i)
        if (!strcmp(properties[i].extensionName, name)) return 1;
    return 0;
}

static int check_device(PFN_vkGetInstanceProcAddr get, int api_1_0)
{
    VkInstance instance = NULL;
    VkPhysicalDevice *devices = NULL;
    VkExtensionProperties *extensions = NULL;
    VkQueueFamilyProperties *families = NULL;
    VkDevice device = NULL;
    VkResult result;
    uint32_t count = 0, extension_count = 0, family_count = 0, family;
    const char *subset = "VK_KHR_portability_subset";
    float priority = 1.0f;
    int status = 1;
    VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
                            .pApplicationName = "Gluon Vulkan probe",
                            .apiVersion = api_1_0 ? VK_API_VERSION_1_0 : VK_API_VERSION_1_1};
    VkInstanceCreateInfo instance_info = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
                                         .pApplicationInfo = &app};
    PFN_vkCreateInstance create = (PFN_vkCreateInstance)get(NULL, "vkCreateInstance");
    if (!create || (result = create(&instance_info, NULL, &instance)) != VK_SUCCESS)
    {
        fprintf(stderr, "vkCreateInstance failed: %d\n", create ? result : VK_ERROR_INITIALIZATION_FAILED);
        if (create && result == VK_ERROR_INCOMPATIBLE_DRIVER)
        {
            fputs("GLUON_PROBE_UNAVAILABLE: Vulkan driver has no usable Metal device\n", stderr);
            return 77;
        }
        return 1;
    }
#define INSTANCE_PROC(type, variable, name) \
    type variable = (type)get(instance, name); \
    if (!variable) { fprintf(stderr, "Missing %s\n", name); goto done; }
    INSTANCE_PROC(PFN_vkDestroyInstance, destroy_instance, "vkDestroyInstance")
    INSTANCE_PROC(PFN_vkEnumeratePhysicalDevices, enumerate, "vkEnumeratePhysicalDevices")
    INSTANCE_PROC(PFN_vkGetPhysicalDeviceProperties, properties, "vkGetPhysicalDeviceProperties")
    INSTANCE_PROC(PFN_vkGetPhysicalDeviceQueueFamilyProperties, queues, "vkGetPhysicalDeviceQueueFamilyProperties")
    INSTANCE_PROC(PFN_vkEnumerateDeviceExtensionProperties, enumerate_extensions, "vkEnumerateDeviceExtensionProperties")
    INSTANCE_PROC(PFN_vkCreateDevice, create_device, "vkCreateDevice")
    INSTANCE_PROC(PFN_vkGetDeviceProcAddr, device_get, "vkGetDeviceProcAddr")
#undef INSTANCE_PROC
    result = enumerate(instance, &count, NULL);
    if (result != VK_SUCCESS) goto done;
    if (!count)
    {
        fputs("GLUON_PROBE_UNAVAILABLE: Vulkan reports no physical device\n", stderr);
        status = 77;
        goto done;
    }
    if (!(devices = calloc(count, sizeof(*devices)))) goto done;
    if ((result = enumerate(instance, &count, devices)) != VK_SUCCESS || !count) goto done;

    VkPhysicalDeviceProperties props;
    properties(devices[0], &props);
    printf("Device: %s, Vulkan %u.%u.%u\n", props.deviceName, VK_VERSION_MAJOR(props.apiVersion),
           VK_VERSION_MINOR(props.apiVersion), VK_VERSION_PATCH(props.apiVersion));
    if ((result = enumerate_extensions(devices[0], NULL, &extension_count, NULL)) != VK_SUCCESS) goto done;
    if (extension_count)
    {
        if (!(extensions = calloc(extension_count, sizeof(*extensions)))) goto done;
        if ((result = enumerate_extensions(devices[0], NULL, &extension_count, extensions)) != VK_SUCCESS) goto done;
    }
    queues(devices[0], &family_count, NULL);
    if (!family_count || !(families = calloc(family_count, sizeof(*families)))) goto done;
    queues(devices[0], &family_count, families);
    for (family = 0; family < family_count; ++family)
        if (families[family].queueCount && (families[family].queueFlags & VK_QUEUE_GRAPHICS_BIT)) break;
    if (family == family_count) goto done;
    VkDeviceQueueCreateInfo queue_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
                                         .queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority};
    VkDeviceCreateInfo device_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
                                     .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info};
    /* Required by the portability subset specification when the host advertises it. */
    if (has_extension(extensions, extension_count, subset))
    {
        device_info.enabledExtensionCount = 1;
        device_info.ppEnabledExtensionNames = &subset;
    }
    if ((result = create_device(devices[0], &device_info, NULL, &device)) != VK_SUCCESS) goto done;
    PFN_vkDeviceWaitIdle wait = (PFN_vkDeviceWaitIdle)device_get(device, "vkDeviceWaitIdle");
    PFN_vkDestroyDevice destroy_device = (PFN_vkDestroyDevice)device_get(device, "vkDestroyDevice");
    if (!wait || !destroy_device) goto done;
    result = wait(device);
    destroy_device(device, NULL);
    device = NULL;
    if (result != VK_SUCCESS) goto done;
    puts("Vulkan device creation and wait passed");
    status = 0;
done:
    if (status == 1) fprintf(stderr, "Vulkan device probe failed (last VkResult %d)\n", result);
    /* A missing mandatory driver function is an unrecoverable probe failure. */
    if (destroy_instance) destroy_instance(instance, NULL);
    free(families);
    free(extensions);
    free(devices);
    return status;
}

int main(int argc, char **argv)
{
    PFN_vkGetInstanceProcAddr get;
    PFN_vkEnumerateInstanceExtensionProperties enumerate;
    PFN_vkEnumerateInstanceVersion version;
    VkExtensionProperties *extensions = NULL;
    VkResult result;
    uint32_t count = 0, api = VK_API_VERSION_1_0;
    int device_check = 0, api_1_0 = 0, status = 1;
    /* Keep the last completed stage visible if a driver call blocks. */
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
    puts("Loading Vulkan library");
#ifdef WINE_UNIX_LIB
    if (argc < 2 || argc > 3 || (argc == 3 && strcmp(argv[2], "--device") && strcmp(argv[2], "--device-1.0"))) return 2;
    void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!library) { fprintf(stderr, "dlopen: %s\n", dlerror()); return 1; }
    get = (PFN_vkGetInstanceProcAddr)dlsym(library, "vkGetInstanceProcAddr");
    device_check = argc == 3;
    api_1_0 = argc == 3 && !strcmp(argv[2], "--device-1.0");
#else
    if (argc > 2 || (argc == 2 && strcmp(argv[1], "--device") && strcmp(argv[1], "--device-1.0"))) return 2;
    HMODULE library = LoadLibraryA("vulkan-1.dll");
    if (!library) { fprintf(stderr, "LoadLibrary(vulkan-1.dll): %lu\n", (unsigned long)GetLastError()); return 1; }
    get = (PFN_vkGetInstanceProcAddr)GetProcAddress(library, "vkGetInstanceProcAddr");
    device_check = argc == 2;
    api_1_0 = argc == 2 && !strcmp(argv[1], "--device-1.0");
#endif
    if (!get) { fputs("Missing vkGetInstanceProcAddr\n", stderr); goto done; }
    puts("Resolving Vulkan global functions");
    enumerate = (PFN_vkEnumerateInstanceExtensionProperties)get(NULL, "vkEnumerateInstanceExtensionProperties");
    version = (PFN_vkEnumerateInstanceVersion)get(NULL, "vkEnumerateInstanceVersion");
    if (!enumerate) { fputs("Missing vkEnumerateInstanceExtensionProperties\n", stderr); goto done; }
    puts("Querying Vulkan instance version and extensions");
    if (version && (result = version(&api)) != VK_SUCCESS) goto done;
    if ((result = enumerate(NULL, &count, NULL)) != VK_SUCCESS || !count) goto done;
    if (!(extensions = calloc(count, sizeof(*extensions)))) goto done;
    if ((result = enumerate(NULL, &count, extensions)) != VK_SUCCESS) goto done;
    if (!has_extension(extensions, count, "VK_KHR_surface"))
    {
        fputs("Missing VK_KHR_surface\n", stderr);
        goto done;
    }
#ifdef WINE_UNIX_LIB
    if (!has_extension(extensions, count, "VK_EXT_metal_surface"))
    {
        fputs("Missing VK_EXT_metal_surface\n", stderr);
        goto done;
    }
#else
    if (!has_extension(extensions, count, "VK_KHR_win32_surface"))
    {
        fputs("Missing VK_KHR_win32_surface\n", stderr);
        goto done;
    }
#endif
    printf("Vulkan %u.%u.%u: %u instance extensions, required surface extensions present\n",
           VK_VERSION_MAJOR(api), VK_VERSION_MINOR(api), VK_VERSION_PATCH(api), count);
    status = device_check ? check_device(get, api_1_0) : 0;
done:
    if (status) fputs("Vulkan probe failed\n", stderr);
    free(extensions);
#ifdef WINE_UNIX_LIB
    dlclose(library);
#else
    FreeLibrary(library);
#endif
    return status;
}
