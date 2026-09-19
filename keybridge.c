/*
 * keybridge.exe - Bongo Cat key bridge, Windows side.
 *
 * Runs INSIDE Bongo Cat's Proton prefix (bongo-launch.sh starts it through
 * PROTON_REMOTE_DEBUG_CMD, so it shares Bongo Cat's wineserver).
 *
 * Why it exists: on Linux, Wine only receives keyboard input while one of its
 * own windows is focused, so Bongo Cat can't "hear" you typing in Firefox,
 * Discord, etc. keyreader.py (Linux side) reads your keyboard and sends
 * anonymous taps here over 127.0.0.1; this program replays them with
 * SendInput() so Bongo Cat's global keyboard hook sees them.
 *
 * Protocol (one byte per event):  'A'..'Z' = key down,  'a'..'z' = key up.
 * Anything else is ignored, so nothing but plain letter keys can ever be
 * injected (no F-keys, no modifiers, no Alt+F4).
 *
 * If Wine itself is receiving real input (you clicked on / are typing into the
 * Bongo Cat window), forwarding pauses so keys aren't counted twice.
 *
 * Click fix (BONGO_FORCE_CLICKABLE=1): with Bongo Cat's Transparency Fix (F3)
 * on, the see-through parts are real holes in the window, so the extra
 * "click-through" flag Bongo Cat sets isn't needed - but on Wayland Bongo Cat
 * can't see the mouse to switch it off again, so the cat can never be clicked.
 * This clears that flag on Bongo Cat's colour-keyed window: the cat itself
 * becomes clickable, the see-through area still passes clicks through.
 *
 * Build:  x86_64-w64-mingw32-gcc -O2 -s -mwindows -o keybridge.exe keybridge.c -lws2_32
 */
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <wchar.h>

#define DEFAULT_PORT      47811
#define BRIDGE_MAGIC      ((ULONG_PTR)0x0B0C0CA7u)  /* tags our own injected events */
#define FOCUS_GRACE_MS    1500                       /* pause after real Wine input */

static volatile LONG g_last_real_input = 0;  /* GetTickCount() of last real key/click Wine saw */
static FILE *g_log = NULL;
static unsigned short g_port = DEFAULT_PORT;
static BOOL g_force_clickable = FALSE;

static void logmsg(const char *fmt, ...)
{
    SYSTEMTIME t;
    va_list ap;
    if (!g_log) return;
    GetLocalTime(&t);
    fprintf(g_log, "[%02u:%02u:%02u] ", t.wHour, t.wMinute, t.wSecond);
    va_start(ap, fmt);
    vfprintf(g_log, fmt, ap);
    va_end(ap);
    fputc('\n', g_log);
    fflush(g_log);
}

static void open_log(void)
{
    WCHAR path[MAX_PATH + 16];
    DWORD n = GetModuleFileNameW(NULL, path, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) return;
    WCHAR *dot = wcsrchr(path, L'.');
    if (dot) wcscpy(dot, L".log"); else wcscat(path, L".log");
    g_log = _wfopen(path, L"w");
}

/* ---- Low-level hooks: notice when Wine gets REAL input (its window is focused) ---- */

static void mark_real_input(void)
{
    LONG now = (LONG)GetTickCount();
    if (now == 0) now = 1;
    InterlockedExchange(&g_last_real_input, now);
}

static LRESULT CALLBACK kb_hook(int code, WPARAM wp, LPARAM lp)
{
    if (code == HC_ACTION) {
        const KBDLLHOOKSTRUCT *k = (const KBDLLHOOKSTRUCT *)lp;
        if (k->dwExtraInfo != BRIDGE_MAGIC && !(k->flags & LLKHF_INJECTED))
            mark_real_input();
    }
    return CallNextHookEx(NULL, code, wp, lp);
}

static LRESULT CALLBACK mouse_hook(int code, WPARAM wp, LPARAM lp)
{
    if (code == HC_ACTION && wp != WM_MOUSEMOVE && wp != WM_MOUSEWHEEL && wp != WM_MOUSEHWHEEL) {
        const MSLLHOOKSTRUCT *m = (const MSLLHOOKSTRUCT *)lp;
        if (m->dwExtraInfo != BRIDGE_MAGIC && !(m->flags & LLMHF_INJECTED))
            mark_real_input();
    }
    return CallNextHookEx(NULL, code, wp, lp);
}

static BOOL wine_has_focus(void)
{
    LONG last = g_last_real_input;
    if (last == 0) return FALSE;
    return (DWORD)GetTickCount() - (DWORD)last < FOCUS_GRACE_MS;
}

/* ---- Injection ---- */

static BOOL g_down_sent[26];  /* which letters we currently hold down */

static void send_key(WORD vk, BOOL up)
{
    INPUT in;
    ZeroMemory(&in, sizeof(in));
    in.type = INPUT_KEYBOARD;
    in.ki.wVk = vk;
    in.ki.wScan = (WORD)MapVirtualKeyW(vk, MAPVK_VK_TO_VSC);
    in.ki.dwFlags = up ? KEYEVENTF_KEYUP : 0;
    in.ki.dwExtraInfo = BRIDGE_MAGIC;
    if (SendInput(1, &in, sizeof(in)) != 1)
        logmsg("SendInput failed (error %lu)", GetLastError());
}

static void release_all(void)
{
    for (int i = 0; i < 26; i++) {
        if (g_down_sent[i]) {
            send_key((WORD)('A' + i), TRUE);
            g_down_sent[i] = FALSE;
        }
    }
}

static void handle_byte(unsigned char c)
{
    if (c >= 'A' && c <= 'Z') {
        int i = c - 'A';
        if (wine_has_focus()) return;          /* Wine already gets the real key */
        if (g_down_sent[i]) send_key((WORD)c, TRUE); /* make every press a fresh one */
        send_key((WORD)c, FALSE);
        g_down_sent[i] = TRUE;
    } else if (c >= 'a' && c <= 'z') {
        int i = c - 'a';
        if (!g_down_sent[i]) return;            /* we never pressed it */
        send_key((WORD)(c - 'a' + 'A'), TRUE);  /* always release what we pressed */
        g_down_sent[i] = FALSE;
    }
    /* anything else: ignored on purpose */
}

/* ---- TCP server (127.0.0.1 only) ---- */

static DWORD WINAPI server_thread(LPVOID unused)
{
    (void)unused;
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        logmsg("WSAStartup failed");
        return 1;
    }

    for (;;) {
        SOCKET ls = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (ls == INVALID_SOCKET) { logmsg("socket() failed: %d", WSAGetLastError()); Sleep(2000); continue; }

        struct sockaddr_in addr;
        ZeroMemory(&addr, sizeof(addr));
        addr.sin_family = AF_INET;
        addr.sin_port = htons(g_port);
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);

        if (bind(ls, (struct sockaddr *)&addr, sizeof(addr)) == SOCKET_ERROR ||
            listen(ls, 1) == SOCKET_ERROR) {
            logmsg("cannot listen on 127.0.0.1:%u (error %d) - retrying", g_port, WSAGetLastError());
            closesocket(ls);
            Sleep(2000);
            continue;
        }
        logmsg("listening on 127.0.0.1:%u", g_port);

        for (;;) {
            SOCKET cs = accept(ls, NULL, NULL);
            if (cs == INVALID_SOCKET) { logmsg("accept() failed: %d", WSAGetLastError()); Sleep(500); continue; }
            logmsg("keyreader connected");

            unsigned char buf[256];
            int n;
            while ((n = recv(cs, (char *)buf, sizeof(buf), 0)) > 0)
                for (int i = 0; i < n; i++) handle_byte(buf[i]);

            release_all();
            closesocket(cs);
            logmsg("keyreader disconnected");
        }
    }
    return 0;
}

/* ---- Click fix ---- */

static DWORD g_self_pid;
static int g_fixed_now;          /* windows fixed during the current scan */
static HWND g_logged_hwnd;
static LONG g_fix_count;

static BOOL CALLBACK fix_window(HWND hwnd, LPARAM unused)
{
    (void)unused;
    DWORD pid = 0;
    if (!IsWindowVisible(hwnd)) return TRUE;
    GetWindowThreadProcessId(hwnd, &pid);
    if (pid == g_self_pid) return TRUE;

    LONG_PTR ex = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
    if (!(ex & WS_EX_LAYERED) || !(ex & WS_EX_TRANSPARENT)) return TRUE;

    COLORREF key = 0; BYTE alpha = 0; DWORD flags = 0;
    if (!GetLayeredWindowAttributes(hwnd, &key, &alpha, &flags) || !(flags & LWA_COLORKEY))
        return TRUE;   /* only with Transparency Fix on - otherwise the whole window would block clicks */

    SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex & ~WS_EX_TRANSPARENT);
    g_fixed_now++;
    g_fix_count++;
    if (hwnd != g_logged_hwnd) {
        g_logged_hwnd = hwnd;
        logmsg("click fix: made window %p clickable (Transparency Fix colour %06lx)",
               (void *)hwnd, (unsigned long)key);
    }
    return TRUE;
}

static DWORD WINAPI click_fix_thread(LPVOID unused)
{
    (void)unused;
    DWORD window_start = GetTickCount();
    LONG window_count = 0;
    BOOL warned = FALSE;
    g_self_pid = GetCurrentProcessId();
    logmsg("click fix on");
    for (;;) {
        g_fixed_now = 0;
        EnumWindows(fix_window, 0);
        window_count += g_fixed_now;
        if (GetTickCount() - window_start > 5000) {
            if (window_count > 50 && !warned) {
                logmsg("click fix: Bongo Cat keeps turning click-through back on (%ld times in 5 s)",
                       window_count);
                warned = TRUE;
            }
            window_start = GetTickCount();
            window_count = 0;
        }
        Sleep(50);
    }
    return 0;
}

int WINAPI WinMain(HINSTANCE hinst, HINSTANCE prev, LPSTR cmd, int show)
{
    (void)prev; (void)cmd; (void)show;

    HANDLE mutex = CreateMutexW(NULL, TRUE, L"Local\\BongoCatKeyBridge");
    if (mutex && GetLastError() == ERROR_ALREADY_EXISTS) return 0;  /* already running */

    open_log();
    const char *p = getenv("BONGO_BRIDGE_PORT");
    if (p && atoi(p) > 0 && atoi(p) < 65536) g_port = (unsigned short)atoi(p);
    const char *fc = getenv("BONGO_FORCE_CLICKABLE");
    g_force_clickable = fc && fc[0] == '1';
    const char *kb = getenv("BONGO_KEY_BRIDGE");
    BOOL key_bridge = !kb || kb[0] != '0';
    logmsg("keybridge started (port %u, key bridge %s, click fix %s)", g_port,
           key_bridge ? "on" : "off", g_force_clickable ? "on" : "off");

    if (g_force_clickable && !CreateThread(NULL, 0, click_fix_thread, NULL, 0, NULL))
        logmsg("could not start click fix thread");

    if (!SetWindowsHookExW(WH_KEYBOARD_LL, kb_hook, hinst, 0))
        logmsg("keyboard hook failed (%lu) - double-count guard off", GetLastError());
    if (!SetWindowsHookExW(WH_MOUSE_LL, mouse_hook, hinst, 0))
        logmsg("mouse hook failed (%lu)", GetLastError());

    if (key_bridge && !CreateThread(NULL, 0, server_thread, NULL, 0, NULL)) {
        logmsg("CreateThread failed");
        return 1;
    }

    /* Low-level hooks need a message loop on the thread that installed them. */
    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return 0;
}
