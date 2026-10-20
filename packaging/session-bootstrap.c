/* Neutral GUI/service initialization for Gluon's bounded Wine session.
 * Built against the exact same Wine tree as steam.exe. No Steam identity,
 * command execution, shell expansion, visible application UI or save access.
 */
#include <windows.h>

int WINAPI WinMain(HINSTANCE instance, HINSTANCE previous, char *command, int show)
{
    (void)instance; (void)previous; (void)command; (void)show;
    return GetDesktopWindow() ? 0 : 1;
}
