/*
 * Headless console launcher for the Source 2006 Windows dedicated server.
 *
 * Valve's srcds.exe is a SUBSYSTEM:WINDOWS (GUI) stub that loads
 * bin\dedicated.dll and calls DedicatedMain. Because it is a GUI PE, Wine
 * detaches stdio and CTextConsoleWin32::Init calls AllocConsole(), spawning
 * conhost.exe on X11 and sending console output to an invisible window instead
 * of container stdout (while DedicatedSpewOutputFunc replaces engine.dll's
 * -condebug logger before a map ever loads).
 *
 * Linked as SUBSYSTEM:CONSOLE instead, Wine maps container fds 1 and 2 directly
 * to STD_OUTPUT_HANDLE and STD_ERROR_HANDLE. Patching dedicated.dll's imports:
 *   - keeps stdout on fd 1 (AllocConsole) and suppresses the 80-column status
 *     bar CTextConsoleWin32::WriteStatusLine draws at (0, 0) when a TTY is
 *     attached (WriteConsoleOutput*, SetConsoleTitleA);
 *   - drops dedicated.dll's no-op CtrlHandler (which returns TRUE and swallows
 *     Ctrl-C) and satisfies GetLine's console input poll on non-TTY stdin;
 *   - turns modal Engine Error dialogs into stderr output.
 *
 * Because /usr/bin/wine runs as PID 1 in the container and leaves SIGTERM at
 * SIG_DFL (which the Linux kernel ignores for PID 1), main() also installs
 * Linux i386 signal handlers for SIGINT and SIGTERM alongside a Win32 console
 * control handler so Ctrl-C and `docker stop` exit immediately with 130 / 143.
 */

#define WIN32_LEAN_AND_MEAN
#include <stdio.h>
#include <string.h>
#include <windows.h>

#define DEDICATED_PATH "bin\\dedicated.dll"

#define LINUX_SIGINT 2
#define LINUX_SIGTERM 15
#define LINUX_SA_ONSTACK 0x08000000UL
#define LINUX_NR_RT_SIGACTION 174
#define LINUX_NR_EXIT_GROUP 252

typedef int(__cdecl *DedicatedMain_fn)(HINSTANCE instance, HINSTANCE prev,
                                       LPSTR cmd_line, int cmd_show);

struct linux_sigaction {
    void(__cdecl *handler)(int);
    unsigned long flags;
    void(__cdecl *restorer)(void);
    unsigned long mask[2];
};

__attribute__((noreturn)) static void exit_group(int code) {
    __asm__ volatile("int $0x80" : : "a"(LINUX_NR_EXIT_GROUP), "b"(code));
    __builtin_unreachable();
}

static void __cdecl exit_on_signal(int sig) { exit_group(128 + sig); }

static void install_linux_signal(int sig) {
    struct linux_sigaction sa = {
        .handler = exit_on_signal,
        .flags = LINUX_SA_ONSTACK,
    };
    int sysnr = LINUX_NR_RT_SIGACTION;
    __asm__ volatile("int $0x80"
                     : "+a"(sysnr)
                     : "b"(sig), "c"(&sa), "d"(0), "S"(sizeof(sa.mask))
                     : "memory");
}

static BOOL WINAPI ctrl_handler(DWORD ctrl_type) {
    exit_group(ctrl_type == CTRL_C_EVENT || ctrl_type == CTRL_BREAK_EVENT
                   ? 128 + LINUX_SIGINT
                   : 128 + LINUX_SIGTERM);
}

static BOOL WINAPI hook_AllocConsole(void) { return TRUE; }

static BOOL WINAPI hook_SetConsoleTitleA(LPCSTR title) {
    (void)title;
    return TRUE;
}

static BOOL WINAPI hook_SetConsoleCtrlHandler(PHANDLER_ROUTINE handler,
                                              BOOL add) {
    (void)handler;
    (void)add;
    return TRUE;
}

static BOOL WINAPI hook_GetNumberOfConsoleInputEvents(HANDLE in,
                                                      LPDWORD count) {
    (void)in;
    *count = 0;
    return TRUE;
}

static BOOL WINAPI hook_WriteConsoleOutputCharacterA(HANDLE out, LPCSTR chars,
                                                     DWORD len, COORD coord,
                                                     LPDWORD written) {
    (void)out;
    (void)chars;
    (void)coord;
    if (written) *written = len;
    return TRUE;
}

static BOOL WINAPI hook_WriteConsoleOutputAttribute(HANDLE out,
                                                    const WORD *attrs,
                                                    DWORD len, COORD coord,
                                                    LPDWORD written) {
    (void)out;
    (void)attrs;
    (void)coord;
    if (written) *written = len;
    return TRUE;
}

static int WINAPI hook_MessageBoxA(HWND hwnd, LPCSTR text, LPCSTR caption,
                                   UINT type) {
    (void)hwnd;
    (void)type;
    fprintf(stderr, "\nFATAL [%s]: %s\n", caption ? caption : "Error",
            text ? text : "");
    fflush(stderr);
    return IDOK;
}

static const struct {
    const char *name;
    FARPROC fn;
} kHooks[] = {
    {"AllocConsole", (FARPROC)hook_AllocConsole},
    {"SetConsoleTitleA", (FARPROC)hook_SetConsoleTitleA},
    {"SetConsoleCtrlHandler", (FARPROC)hook_SetConsoleCtrlHandler},
    {"GetNumberOfConsoleInputEvents",
     (FARPROC)hook_GetNumberOfConsoleInputEvents},
    {"WriteConsoleOutputCharacterA",
     (FARPROC)hook_WriteConsoleOutputCharacterA},
    {"WriteConsoleOutputAttribute", (FARPROC)hook_WriteConsoleOutputAttribute},
    {"MessageBoxA", (FARPROC)hook_MessageBoxA},
};

static void patch_iat(HMODULE mod) {
    BYTE *base = (BYTE *)mod;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(base + dos->e_lfanew);
    DWORD rva =
        nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT]
            .VirtualAddress;
    if (!rva) return;

    for (IMAGE_IMPORT_DESCRIPTOR *d = (IMAGE_IMPORT_DESCRIPTOR *)(base + rva);
         d->Name; d++) {
        IMAGE_THUNK_DATA *orig =
            (IMAGE_THUNK_DATA *)(base + (d->OriginalFirstThunk
                                             ? d->OriginalFirstThunk
                                             : d->FirstThunk));
        IMAGE_THUNK_DATA *iat = (IMAGE_THUNK_DATA *)(base + d->FirstThunk);
        for (; orig->u1.AddressOfData; orig++, iat++) {
            if (IMAGE_SNAP_BY_ORDINAL(orig->u1.Ordinal)) continue;
            const char *sym =
                (const char *)((IMAGE_IMPORT_BY_NAME *)(base +
                                                        orig->u1.AddressOfData))
                    ->Name;
            for (size_t i = 0; i < sizeof(kHooks) / sizeof(kHooks[0]); i++) {
                if (strcmp(sym, kHooks[i].name) == 0) {
                    DWORD prot = 0;
                    VirtualProtect(&iat->u1.Function, sizeof(iat->u1.Function),
                                   PAGE_READWRITE, &prot);
                    iat->u1.Function = (ULONG_PTR)kHooks[i].fn;
                    VirtualProtect(&iat->u1.Function, sizeof(iat->u1.Function),
                                   prot, &prot);
                }
            }
        }
    }
}

int main(void) {
    SetConsoleCtrlHandler(ctrl_handler, TRUE);
    install_linux_signal(LINUX_SIGINT);
    install_linux_signal(LINUX_SIGTERM);

    char path[4096] = "bin;";
    GetEnvironmentVariableA("PATH", path + 4, sizeof(path) - 4);
    SetEnvironmentVariableA("PATH", path);

    HMODULE dedicated = LoadLibraryA(DEDICATED_PATH);
    if (!dedicated) {
        fprintf(stderr, "Failed to open %s (error %lu)\n", DEDICATED_PATH,
                GetLastError());
        return 1;
    }
    patch_iat(dedicated);

    DedicatedMain_fn dedicated_main =
        (DedicatedMain_fn)GetProcAddress(dedicated, "DedicatedMain");
    if (!dedicated_main) {
        fprintf(stderr, "Failed to find DedicatedMain\n");
        return 1;
    }

    return dedicated_main(GetModuleHandleA(NULL), NULL, (LPSTR)"", SW_HIDE);
}
