/* Account-free descendants and real service dispatch for CI Wine lifecycle. */
#include <windows.h>
#include <stdio.h>
#include <string.h>

static char ready_path[2048], release_path[2048];
static SERVICE_STATUS_HANDLE status_handle;
static SERVICE_STATUS service_status;

static int marker(const char *path)
{
    DWORD written;
    HANDLE file = CreateFileA(path, GENERIC_WRITE, FILE_SHARE_READ, NULL,
                              CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    BOOL ok;
    if (file == INVALID_HANDLE_VALUE) return 1;
    ok = WriteFile(file, "saved\n", 6, &written, NULL) && written == 6 && FlushFileBuffers(file);
    CloseHandle(file);
    return !ok;
}

static int hold(const char *ready, const char *release)
{
    char done[2100];
    DWORD start = GetTickCount();
    if (marker(ready)) return 2;
    while (GetFileAttributesA(release) == INVALID_FILE_ATTRIBUTES)
    {
        if (GetTickCount() - start > 20000) return 3;
        Sleep(10);
    }
    if (snprintf(done, sizeof(done), "%s.done", ready) >= (int)sizeof(done)) return 4;
    return marker(done);
}

static void WINAPI control(DWORD code)
{
    if (code == SERVICE_CONTROL_STOP || code == SERVICE_CONTROL_SHUTDOWN)
    {
        service_status.dwCurrentState = SERVICE_STOP_PENDING;
        service_status.dwWaitHint = 1000;
        SetServiceStatus(status_handle, &service_status);
    }
}

static void WINAPI service_main(DWORD argc, char **argv)
{
    int result;
    (void)argc; (void)argv;
    status_handle = RegisterServiceCtrlHandlerA("GluonCILifecycle", control);
    if (!status_handle) return;
    service_status.dwServiceType = SERVICE_WIN32_OWN_PROCESS;
    service_status.dwCurrentState = SERVICE_RUNNING;
    service_status.dwControlsAccepted = SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN;
    SetServiceStatus(status_handle, &service_status);
    result = hold(ready_path, release_path);
    service_status.dwCurrentState = SERVICE_STOPPED;
    service_status.dwWin32ExitCode = result ? ERROR_SERVICE_SPECIFIC_ERROR : NO_ERROR;
    service_status.dwServiceSpecificExitCode = result;
    SetServiceStatus(status_handle, &service_status);
}

int main(int argc, char **argv)
{
    char image[2048], command[6500];
    STARTUPINFOA startup = {0};
    PROCESS_INFORMATION process;
    if (argc != 4 || !GetModuleFileNameA(NULL, image, sizeof(image))) return 10;
    if (!strcmp(argv[1], "--hold")) return hold(argv[2], argv[3]);
    if (!strcmp(argv[1], "--service"))
    {
        SERVICE_TABLE_ENTRYA table[] = {{"GluonCILifecycle", service_main}, {NULL, NULL}};
        if (strlen(argv[2]) >= sizeof(ready_path) || strlen(argv[3]) >= sizeof(release_path)) return 11;
        strcpy(ready_path, argv[2]); strcpy(release_path, argv[3]);
        return StartServiceCtrlDispatcherA(table) ? 0 : 12;
    }
    if (snprintf(command, sizeof(command), "\"%s\" %s \"%s\" \"%s\"", image,
                 !strcmp(argv[1], "--start-service") ? "--service" : "--hold", argv[2], argv[3]) >= (int)sizeof(command)) return 13;
    if (!strcmp(argv[1], "--start-service"))
    {
        SC_HANDLE manager = OpenSCManagerA(NULL, NULL, SC_MANAGER_CREATE_SERVICE);
        SC_HANDLE service;
        BOOL started;
        if (!manager) return 14;
        service = CreateServiceA(manager, "GluonCILifecycle", "Gluon CI lifecycle", SERVICE_ALL_ACCESS,
                                 SERVICE_WIN32_OWN_PROCESS, SERVICE_DEMAND_START, SERVICE_ERROR_NORMAL,
                                 command, NULL, NULL, NULL, NULL, NULL);
        if (!service) { CloseServiceHandle(manager); return 15; }
        started = StartServiceA(service, 0, NULL);
        DeleteService(service);
        CloseServiceHandle(service); CloseServiceHandle(manager);
        return started ? 0 : 16;
    }
    if (strcmp(argv[1], "--detach")) return 17;
    startup.cb = sizeof(startup);
    if (!CreateProcessA(image, command, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, NULL, &startup, &process)) return 18;
    CloseHandle(process.hThread); CloseHandle(process.hProcess);
    return 0;
}
