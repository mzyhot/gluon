/* Check Wine's TLS provider and media module without network or graphics. */
#define SECURITY_WIN32
#include <windows.h>
#include <security.h>
#include <schannel.h>
#include <stdio.h>

int main(void)
{
    CredHandle credential;
    TimeStamp expiration;
    SECURITY_STATUS status;
    HMODULE media;

    status = AcquireCredentialsHandleA(NULL, UNISP_NAME_A, SECPKG_CRED_OUTBOUND,
            NULL, NULL, NULL, NULL, &credential, &expiration);
    if (status != SEC_E_OK)
    {
        fprintf(stderr, "Wine TLS credential provider failed: 0x%08lx\n", (unsigned long)status);
        return 1;
    }
    status = FreeCredentialsHandle(&credential);
    if (status != SEC_E_OK)
    {
        fprintf(stderr, "Wine TLS credential cleanup failed: 0x%08lx\n", (unsigned long)status);
        return 1;
    }
    puts("Wine TLS credential provider passed");
    media = LoadLibraryA("winegstreamer.dll");
    if (!media)
    {
        fprintf(stderr, "Wine media module load failed: %lu\n", (unsigned long)GetLastError());
        return 1;
    }
    FreeLibrary(media);
    puts("Wine media module load passed");
    return 0;
}
