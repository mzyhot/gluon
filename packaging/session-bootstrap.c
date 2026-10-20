/* Neutral GUI/service initialization for Gluon's bounded Wine session.
 * Built against the exact same Wine tree as steam.exe. No Steam identity,
 * command execution, shell expansion, visible application UI or save access.
 */
#include <windows.h>

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, WCHAR *command, int show)
{
    return GetDesktopWindow() ? 0 : 1;
}
