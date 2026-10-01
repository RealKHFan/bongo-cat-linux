/*
 * keybridge.exe - Bongo Cat bridge, Windows side.
 *
 * Runs INSIDE Bongo Cat's Proton prefix (bongo-launch.sh starts it through
 * PROTON_REMOTE_DEBUG_CMD, so it shares Bongo Cat's wineserver).
 *
 * Why it exists: on Linux, Wine only receives input while one of its own
 * windows is focused, so Bongo Cat can't "hear" you typing in Firefox,
 * Discord, etc. keyreader.py (Linux side) reads your keyboard and sends
 * anonymous taps here over 127.0.0.1; this program replays them with
 * SendInput() so Bongo Cat's global input hooks see them.
 *
 * Protocol (one byte per event):
 *   'A'..'Z'  tap this letter          'a'..'z'  that letter was let go
 *   '1'       mouse click tap          '0'       mouse button let go
 * Anything else is ignored, so nothing but plain letters and a plain left
 * click can ever be injected (no F-keys, no modifiers, no Alt+F4).
 *
 * Each tap is a real down -> hold -> up cycle with a minimum hold time, so a
 * game that samples key state once a frame still sees it, and taps queued on
 * the same letter are played out one after another instead of merging. That
 * is why keyreader.py spreads your typing over 26 letters: 30 keys at once
 * become 30 separate taps instead of 2.
 *
 * If Wine itself is receiving real input (you clicked on / are typing into
 * the Bongo Cat window), forwarding pauses briefly so taps aren't counted
 * twice.
 *
 * Click fix (BONGO_FORCE_CLICKABLE=1): with Bongo Cat's Transparency Fix (F3)
 * on, the see-through parts are real holes in the window, so the extra
 * "click-through" flag Bongo Cat sets isn't needed - but on Wayland Bongo Cat
 * can't see the mouse to switch it off again, so the cat can never be
 * clicked. This clears that flag on Bongo Cat's colour-keyed window: the cat
 * itself becomes clickable, the see-through area still passes clicks through.
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
#define KEY_GRACE_MS      1500   /* pause keys after real typing into Wine */
#define MOUSE_GRACE_MS     400   /* pause clicks after a real click in Wine */
#define MOUSE_ARM_MS        60   /* let a real click land before replaying ours */
#define TICK_MS              5   /* worker thread resolution */
#define HOLD_MS             55   /* how long an injected tap stays down */
#define GAP_MS              35   /* gap before the same letter taps again (>2 frames) */
#define MAX_QUEUED           8   /* per letter, so a key-mash can't run away */
#define MAX_HIDDEN          16   /* windows we can shield during a click */

static volatile LONG g_last_real_key = 0;    /* GetTickCount() of last real key Wine saw */
static volatile LONG g_last_real_click = 0;  /* ... and last real mouse click */
static FILE *g_log = NULL;
static unsigned short g_port = DEFAULT_PORT;
static BOOL g_force_clickable = FALSE;
static BOOL g_real_mouse = FALSE;
static CRITICAL_SECTION g_lock;

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

static LRESULT CALLBACK kb_hook(int code, WPARAM wp, LPARAM lp)
{
    if (code == HC_ACTION) {
        const KBDLLHOOKSTRUCT *k = (const KBDLLHOOKSTRUCT *)lp;
        if (k->dwExtraInfo != BRIDGE_MAGIC && !(k->flags & LLKHF_INJECTED)) {
            LONG now = (LONG)GetTickCount();
            InterlockedExchange(&g_last_real_key, now ? now : 1);
        }
    }
    return CallNextHookEx(NULL, code, wp, lp);
}

static LRESULT CALLBACK mouse_hook(int code, WPARAM wp, LPARAM lp)
{
    if (code == HC_ACTION && wp != WM_MOUSEMOVE && wp != WM_MOUSEWHEEL && wp != WM_MOUSEHWHEEL) {
        const MSLLHOOKSTRUCT *m = (const MSLLHOOKSTRUCT *)lp;
        if (m->dwExtraInfo != BRIDGE_MAGIC && !(m->flags & LLMHF_INJECTED)) {
            LONG now = (LONG)GetTickCount();
            InterlockedExchange(&g_last_real_click, now ? now : 1);
        }
    }
    return CallNextHookEx(NULL, code, wp, lp);
}

static BOOL recently(volatile LONG *stamp, DWORD window_ms)
{
    LONG last = *stamp;
    if (last == 0) return FALSE;
    return (DWORD)GetTickCount() - (DWORD)last < window_ms;
}

/* ---- Injection ---- */

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

static void send_click(BOOL up)
{
    INPUT in;
    ZeroMemory(&in, sizeof(in));
    in.type = INPUT_MOUSE;
    in.mi.dwFlags = up ? MOUSEEVENTF_LEFTUP : MOUSEEVENTF_LEFTDOWN;
    in.mi.dwExtraInfo = BRIDGE_MAGIC;
    if (SendInput(1, &in, sizeof(in)) != 1)
        logmsg("SendInput (mouse) failed (error %lu)", GetLastError());
}

/* ---- Shielding windows during an injected click -------------------------
 * An injected click is seen by Bongo Cat's global mouse hook no matter what,
 * but it would ALSO land on whatever window sits under Wine's cursor - which
 * on Wayland is usually Bongo Cat itself, so the click could press its
 * buttons. While the injected click is in flight we set the standard
 * click-through flag on Bongo Cat's windows, so the click reaches the hook
 * and then falls through to nothing. Real clicks are unaffected: the shield
 * is up for about one frame.
 */
static HWND g_hidden[MAX_HIDDEN];
static int g_hidden_count;
static DWORD g_self_pid;

static BOOL CALLBACK shield_window(HWND hwnd, LPARAM unused)
{
    DWORD pid = 0;
    LONG_PTR ex;
    (void)unused;
    if (g_hidden_count >= MAX_HIDDEN) return FALSE;
    if (!IsWindowVisible(hwnd)) return TRUE;
    GetWindowThreadProcessId(hwnd, &pid);
    if (pid == g_self_pid) return TRUE;
    ex = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
    if (ex & WS_EX_TRANSPARENT) return TRUE;      /* already passes clicks through */
    SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_TRANSPARENT);
    g_hidden[g_hidden_count++] = hwnd;
    return TRUE;
}

static void shield_up(void)
{
    g_hidden_count = 0;
    EnumWindows(shield_window, 0);
}

static void shield_down(void)
{
    int i;
    for (i = 0; i < g_hidden_count; i++) {
        if (IsWindow(g_hidden[i])) {
            LONG_PTR ex = GetWindowLongPtrW(g_hidden[i], GWL_EXSTYLE);
            SetWindowLongPtrW(g_hidden[i], GWL_EXSTYLE, ex & ~WS_EX_TRANSPARENT);
        }
    }
    g_hidden_count = 0;
}

/* ---- Tap scheduler ------------------------------------------------------
 * One slot per letter plus one for the mouse. A slot plays out queued taps as
 * down -> hold HOLD_MS -> up -> wait GAP_MS -> next, so every tap is a clean
 * state change even for a game that only samples input once a frame.
 */
typedef struct {
    int queued;        /* taps still owed */
    BOOL down;
    DWORD stamp;       /* when the current phase started */
    DWORD armed;       /* when the oldest queued tap arrived (mouse only) */
} Slot;

static Slot g_keys[26];
static Slot g_mouse;
static LONG g_taps_done;

static void queue_tap(Slot *s)
{
    if (s->queued >= MAX_QUEUED) return;
    if (s->queued == 0) s->armed = GetTickCount();
    s->queued++;
}

static void pump_slot(Slot *s, DWORD now, int index)   /* index -1 = mouse */
{
    if (s->down) {
        if (now - s->stamp < HOLD_MS) return;
        if (index >= 0) {
            send_key((WORD)('A' + index), TRUE);
        } else {
            send_click(TRUE);
            shield_down();
        }
        s->down = FALSE;
        s->stamp = now;
        return;
    }
    if (s->queued <= 0 || now - s->stamp < GAP_MS) return;
    if (index >= 0) {
        if (recently(&g_last_real_key, KEY_GRACE_MS)) { s->queued = 0; return; }
        send_key((WORD)('A' + index), FALSE);
    } else {
        /* Wait a moment before clicking. If you clicked the cat itself, Wine
           gets that click for real at almost the same instant; this makes
           sure the real one always lands first so we can drop ours instead
           of counting the click twice. */
        if (now - s->armed < MOUSE_ARM_MS) return;
        if (recently(&g_last_real_click, MOUSE_GRACE_MS)) { s->queued = 0; return; }
        shield_up();
        send_click(FALSE);
    }
    s->queued--;
    s->down = TRUE;
    s->stamp = now;
    g_taps_done++;
}

static void release_all(void)
{
    int i;
    DWORD now = GetTickCount();
    for (i = 0; i < 26; i++) {
        g_keys[i].queued = 0;
        if (g_keys[i].down) { send_key((WORD)('A' + i), TRUE); g_keys[i].down = FALSE; g_keys[i].stamp = now; }
    }
    g_mouse.queued = 0;
    if (g_mouse.down) { send_click(TRUE); shield_down(); g_mouse.down = FALSE; g_mouse.stamp = now; }
}

static void handle_byte(unsigned char c)
{
    EnterCriticalSection(&g_lock);
    if (c >= 'A' && c <= 'Z')      queue_tap(&g_keys[c - 'A']);
    else if (c == '1')             { if (g_real_mouse) queue_tap(&g_mouse); }
    /* lowercase letters and '0' are "let go" hints; the tap cycle already ends
       by itself, so there is nothing to do and nothing can stay stuck down. */
    LeaveCriticalSection(&g_lock);
}

/* ---- Click fix ---- */

static int g_fixed_now;
static HWND g_logged_hwnd;

static BOOL CALLBACK fix_window(HWND hwnd, LPARAM unused)
{
    DWORD pid = 0;
    COLORREF key = 0;
    BYTE alpha = 0;
    DWORD flags = 0;
    LONG_PTR ex;
    (void)unused;

    if (!IsWindowVisible(hwnd)) return TRUE;
    GetWindowThreadProcessId(hwnd, &pid);
    if (pid == g_self_pid) return TRUE;

    ex = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
    if (!(ex & WS_EX_LAYERED) || !(ex & WS_EX_TRANSPARENT)) return TRUE;

    if (!GetLayeredWindowAttributes(hwnd, &key, &alpha, &flags) || !(flags & LWA_COLORKEY))
        return TRUE;   /* only with Transparency Fix on - otherwise the whole window would block clicks */

    SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex & ~WS_EX_TRANSPARENT);
    g_fixed_now++;
    if (hwnd != g_logged_hwnd) {
        g_logged_hwnd = hwnd;
        logmsg("click fix: made window %p clickable (Transparency Fix colour %06lx)",
               (void *)hwnd, (unsigned long)key);
    }
    return TRUE;
}

/* ---- Worker: runs the tap slots and the click fix in one thread ---- */

static DWORD WINAPI worker_thread(LPVOID unused)
{
    DWORD window_start = GetTickCount();
    LONG window_fixes = 0;
    LONG last_logged_taps = 0;
    DWORD last_tap_log = GetTickCount();
    BOOL warned = FALSE;
    int ticks = 0;
    (void)unused;

    for (;;) {
        DWORD now = GetTickCount();
        int i;

        EnterCriticalSection(&g_lock);
        for (i = 0; i < 26; i++) pump_slot(&g_keys[i], now, i);
        if (g_real_mouse) pump_slot(&g_mouse, now, -1);

        /* the click fix must not run while a click is shielded, or it would
           undo the shield mid-click - same thread, so they can't overlap */
        if (g_force_clickable && !g_mouse.down && ++ticks >= 10) {
            ticks = 0;
            g_fixed_now = 0;
            EnumWindows(fix_window, 0);
            window_fixes += g_fixed_now;
            if (now - window_start > 5000) {
                if (window_fixes > 50 && !warned) {
                    logmsg("click fix: Bongo Cat keeps turning click-through back on (%ld times in 5 s)",
                           window_fixes);
                    warned = TRUE;
                }
                window_start = now;
                window_fixes = 0;
            }
        }

        if (g_taps_done != last_logged_taps && now - last_tap_log > 60000) {
            logmsg("%ld taps replayed so far", g_taps_done);
            last_logged_taps = g_taps_done;
            last_tap_log = now;
        }
        LeaveCriticalSection(&g_lock);

        Sleep(TICK_MS);
    }
    return 0;
}

/* ---- TCP server (127.0.0.1 only) ---- */

static DWORD WINAPI server_thread(LPVOID unused)
{
    WSADATA wsa;
    (void)unused;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        logmsg("WSAStartup failed");
        return 1;
    }

    for (;;) {
        SOCKET ls = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        struct sockaddr_in addr;
        if (ls == INVALID_SOCKET) { logmsg("socket() failed: %d", WSAGetLastError()); Sleep(2000); continue; }

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
            unsigned char buf[256];
            int n, i;
            if (cs == INVALID_SOCKET) { logmsg("accept() failed: %d", WSAGetLastError()); Sleep(500); continue; }
            logmsg("keyreader connected");

            while ((n = recv(cs, (char *)buf, sizeof(buf), 0)) > 0)
                for (i = 0; i < n; i++) handle_byte(buf[i]);

            EnterCriticalSection(&g_lock);
            release_all();
            LeaveCriticalSection(&g_lock);
            closesocket(cs);
            logmsg("keyreader disconnected");
        }
    }
    return 0;
}

int WINAPI WinMain(HINSTANCE hinst, HINSTANCE prev, LPSTR cmd, int show)
{
    const char *p, *fc, *rm, *kb;
    BOOL key_bridge;
    MSG msg;
    HANDLE mutex;
    (void)prev; (void)cmd; (void)show;

    mutex = CreateMutexW(NULL, TRUE, L"Local\\BongoCatKeyBridge");
    if (mutex && GetLastError() == ERROR_ALREADY_EXISTS) return 0;  /* already running */

    open_log();
    InitializeCriticalSection(&g_lock);
    g_self_pid = GetCurrentProcessId();

    p = getenv("BONGO_BRIDGE_PORT");
    if (p && atoi(p) > 0 && atoi(p) < 65536) g_port = (unsigned short)atoi(p);
    fc = getenv("BONGO_FORCE_CLICKABLE");
    g_force_clickable = fc && fc[0] == '1';
    rm = getenv("BONGO_REAL_MOUSE");
    g_real_mouse = rm && rm[0] == '1';
    kb = getenv("BONGO_KEY_BRIDGE");
    key_bridge = !kb || kb[0] != '0';

    logmsg("keybridge started (port %u, key bridge %s, click fix %s, real mouse clicks %s)",
           g_port, key_bridge ? "on" : "off", g_force_clickable ? "on" : "off",
           g_real_mouse ? "on" : "off");

    if (!SetWindowsHookExW(WH_KEYBOARD_LL, kb_hook, hinst, 0))
        logmsg("keyboard hook failed (%lu) - double-count guard off", GetLastError());
    if (!SetWindowsHookExW(WH_MOUSE_LL, mouse_hook, hinst, 0))
        logmsg("mouse hook failed (%lu)", GetLastError());

    if (!CreateThread(NULL, 0, worker_thread, NULL, 0, NULL)) {
        logmsg("could not start worker thread");
        return 1;
    }
    if (key_bridge && !CreateThread(NULL, 0, server_thread, NULL, 0, NULL)) {
        logmsg("could not start server thread");
        return 1;
    }

    /* Low-level hooks need a message loop on the thread that installed them. */
    while (GetMessageW(&msg, NULL, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return 0;
}
