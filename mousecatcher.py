#!/usr/bin/env python3
"""
mousecatcher.py - makes Bongo Cat clickable on Wayland.

The problem: Bongo Cat lets clicks pass through its window and only turns
clickable when it sees the mouse over the cat. On Wayland, Proton games can
only see the mouse while it is over an X11/Proton window, so the cat never
notices you and stays click-through. (That's why it worked with the Steam
window behind it: Steam is an X11 window too.)

The fix: while the cat is click-through, this script puts an invisible X11
window exactly over the cat's pixels. When your mouse touches it, Bongo Cat
can see the mouse, makes itself clickable like on Windows, and the invisible
window immediately gets out of the way.

Needs Bongo Cat's Transparency Fix (F3) on: the cat's outline is read from the
window shape that Transparency Fix creates.

Pure Python standard library (ctypes + the system's libX11/libXext).
    python3 mousecatcher.py --test     # just report what it finds, don't catch
"""
import argparse
import ctypes
import ctypes.util
import os
import select
import signal
import sys
import time

APPID = os.environ.get("SteamAppId") or os.environ.get("SteamGameId") or "3419430"


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


# ---------------------------------------------------------------- Xlib bindings

def _load(names):
    for n in names:
        try:
            return ctypes.CDLL(n)
        except OSError:
            continue
    found = ctypes.util.find_library(names[0].split(".so")[0][3:])
    return ctypes.CDLL(found) if found else None


xlib = _load(["libX11.so.6", "libX11.so"])
xext = _load(["libXext.so.6", "libXext.so"])

Window = ctypes.c_ulong
Atom = ctypes.c_ulong
Display_p = ctypes.c_void_p


class XRectangle(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short),
                ("width", ctypes.c_ushort), ("height", ctypes.c_ushort)]


class XClassHint(ctypes.Structure):
    # raw pointers (not c_char_p) so we can XFree the exact memory Xlib gave us
    _fields_ = [("res_name", ctypes.c_void_p), ("res_class", ctypes.c_void_p)]


class XWindowAttributes(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int),
                ("width", ctypes.c_int), ("height", ctypes.c_int),
                ("border_width", ctypes.c_int), ("depth", ctypes.c_int),
                ("visual", ctypes.c_void_p), ("root", Window),
                ("class_", ctypes.c_int), ("bit_gravity", ctypes.c_int),
                ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int),
                ("backing_planes", ctypes.c_ulong), ("backing_pixel", ctypes.c_ulong),
                ("save_under", ctypes.c_int), ("colormap", ctypes.c_ulong),
                ("map_installed", ctypes.c_int), ("map_state", ctypes.c_int),
                ("all_event_masks", ctypes.c_long), ("your_event_mask", ctypes.c_long),
                ("do_not_propagate_mask", ctypes.c_long), ("override_redirect", ctypes.c_int),
                ("screen", ctypes.c_void_p)]


class XSetWindowAttributes(ctypes.Structure):
    _fields_ = [("background_pixmap", ctypes.c_ulong), ("background_pixel", ctypes.c_ulong),
                ("border_pixmap", ctypes.c_ulong), ("border_pixel", ctypes.c_ulong),
                ("bit_gravity", ctypes.c_int), ("win_gravity", ctypes.c_int),
                ("backing_store", ctypes.c_int), ("backing_planes", ctypes.c_ulong),
                ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int),
                ("event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
                ("override_redirect", ctypes.c_int), ("colormap", ctypes.c_ulong),
                ("cursor", ctypes.c_ulong)]


class XVisualInfo(ctypes.Structure):
    _fields_ = [("visual", ctypes.c_void_p), ("visualid", ctypes.c_ulong),
                ("screen", ctypes.c_int), ("depth", ctypes.c_int), ("class_", ctypes.c_int),
                ("red_mask", ctypes.c_ulong), ("green_mask", ctypes.c_ulong),
                ("blue_mask", ctypes.c_ulong), ("colormap_size", ctypes.c_int),
                ("bits_per_rgb", ctypes.c_int)]


class XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("pad", ctypes.c_long * 24)]


# constants
InputOutput, InputOnly, TrueColor, AllocNone = 1, 2, 4, 0
CWBackPixel, CWBorderPixel, CWOverrideRedirect, CWEventMask, CWColormap = 1 << 1, 1 << 3, 1 << 9, 1 << 11, 1 << 13
EnterWindowMask, PointerMotionMask, ButtonPressMask = 1 << 4, 1 << 6, 1 << 2
MotionNotify, EnterNotify, ButtonPress = 6, 7, 4
IsViewable = 2
ShapeBounding, ShapeInput, ShapeSet, Unsorted = 0, 2, 0, 0
AnyPropertyType, Success = 0, 0

_x_errors = [0]
ErrorHandler = ctypes.CFUNCTYPE(ctypes.c_int, Display_p, ctypes.c_void_p)


@ErrorHandler
def _on_x_error(_dpy, _ev):
    _x_errors[0] += 1      # e.g. BadWindow when Bongo Cat closes mid-query: ignore
    return 0


def _setup_prototypes():
    x, e = xlib, xext
    x.XOpenDisplay.restype = Display_p
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XDefaultRootWindow.restype = Window
    x.XDefaultRootWindow.argtypes = [Display_p]
    x.XDefaultScreen.argtypes = [Display_p]
    x.XInternAtom.restype = Atom
    x.XInternAtom.argtypes = [Display_p, ctypes.c_char_p, ctypes.c_int]
    x.XGetWindowProperty.argtypes = [Display_p, Window, Atom, ctypes.c_long, ctypes.c_long,
                                     ctypes.c_int, Atom, ctypes.POINTER(Atom),
                                     ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
                                     ctypes.POINTER(ctypes.c_ulong),
                                     ctypes.POINTER(ctypes.c_void_p)]
    x.XFree.argtypes = [ctypes.c_void_p]
    x.XGetClassHint.argtypes = [Display_p, Window, ctypes.POINTER(XClassHint)]
    x.XQueryTree.argtypes = [Display_p, Window, ctypes.POINTER(Window), ctypes.POINTER(Window),
                             ctypes.POINTER(ctypes.POINTER(Window)), ctypes.POINTER(ctypes.c_uint)]
    x.XGetWindowAttributes.argtypes = [Display_p, Window, ctypes.POINTER(XWindowAttributes)]
    x.XTranslateCoordinates.argtypes = [Display_p, Window, Window, ctypes.c_int, ctypes.c_int,
                                        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                                        ctypes.POINTER(Window)]
    x.XMatchVisualInfo.argtypes = [Display_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.POINTER(XVisualInfo)]
    x.XCreateColormap.restype = ctypes.c_ulong
    x.XCreateColormap.argtypes = [Display_p, Window, ctypes.c_void_p, ctypes.c_int]
    x.XCreateWindow.restype = Window
    x.XCreateWindow.argtypes = [Display_p, Window, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
                                ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_uint,
                                ctypes.c_void_p, ctypes.c_ulong,
                                ctypes.POINTER(XSetWindowAttributes)]
    x.XStoreName.argtypes = [Display_p, Window, ctypes.c_char_p]
    x.XMapRaised.argtypes = [Display_p, Window]
    x.XUnmapWindow.argtypes = [Display_p, Window]
    x.XDestroyWindow.argtypes = [Display_p, Window]
    x.XMoveResizeWindow.argtypes = [Display_p, Window, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_uint, ctypes.c_uint]
    x.XFlush.argtypes = [Display_p]
    x.XSync.argtypes = [Display_p, ctypes.c_int]
    x.XPending.argtypes = [Display_p]
    x.XNextEvent.argtypes = [Display_p, ctypes.POINTER(XEvent)]
    x.XConnectionNumber.argtypes = [Display_p]
    x.XCloseDisplay.argtypes = [Display_p]
    x.XSetErrorHandler.argtypes = [ErrorHandler]
    x.XSetErrorHandler.restype = ctypes.c_void_p
    e.XShapeQueryExtension.argtypes = [Display_p, ctypes.POINTER(ctypes.c_int),
                                       ctypes.POINTER(ctypes.c_int)]
    e.XShapeGetRectangles.restype = ctypes.POINTER(XRectangle)
    e.XShapeGetRectangles.argtypes = [Display_p, Window, ctypes.c_int,
                                      ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
    e.XShapeCombineRectangles.argtypes = [Display_p, Window, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, ctypes.POINTER(XRectangle), ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int]


# ---------------------------------------------------------------- X helpers

class X:
    def __init__(self):
        self.dpy = xlib.XOpenDisplay(None)
        if not self.dpy:
            raise RuntimeError("cannot open X display %r" % os.environ.get("DISPLAY"))
        xlib.XSetErrorHandler(_on_x_error)
        ev, er = ctypes.c_int(), ctypes.c_int()
        if not xext.XShapeQueryExtension(self.dpy, ctypes.byref(ev), ctypes.byref(er)):
            raise RuntimeError("X server has no SHAPE extension")
        self.root = xlib.XDefaultRootWindow(self.dpy)
        self.screen = xlib.XDefaultScreen(self.dpy)
        self.a_client_list = xlib.XInternAtom(self.dpy, b"_NET_CLIENT_LIST", 0)

    def client_list(self):
        """Managed top-level windows (from the window manager), or a tree walk as fallback."""
        typ, fmt = Atom(), ctypes.c_int()
        n, after, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
        wins = []
        if xlib.XGetWindowProperty(self.dpy, self.root, self.a_client_list, 0, 4096, 0,
                                   AnyPropertyType, ctypes.byref(typ), ctypes.byref(fmt),
                                   ctypes.byref(n), ctypes.byref(after),
                                   ctypes.byref(data)) == Success and data.value:
            arr = ctypes.cast(data, ctypes.POINTER(Window))
            wins = [arr[i] for i in range(n.value)]
            xlib.XFree(data)
        if not wins:   # no window manager list: walk the tree (root > frame > wrapper > client)
            level = [self.root]
            for _ in range(3):
                level = [c for w in level for c in self.children(w)]
                wins.extend(level)
        return wins

    def children(self, win):
        root, parent = Window(), Window()
        kids, n = ctypes.POINTER(Window)(), ctypes.c_uint()
        if not xlib.XQueryTree(self.dpy, win, ctypes.byref(root), ctypes.byref(parent),
                               ctypes.byref(kids), ctypes.byref(n)):
            return []
        out = [kids[i] for i in range(n.value)]
        if n.value:
            xlib.XFree(kids)
        return out

    def wm_class(self, win):
        hint = XClassHint()
        if not xlib.XGetClassHint(self.dpy, win, ctypes.byref(hint)):
            return ""
        parts = []
        for p in (hint.res_name, hint.res_class):
            if p:
                parts.append(ctypes.string_at(p).decode(errors="replace"))
                xlib.XFree(p)
        return " ".join(parts)

    def geometry(self, win):
        """(abs_x, abs_y, width, height, viewable) or None."""
        attr = XWindowAttributes()
        if not xlib.XGetWindowAttributes(self.dpy, win, ctypes.byref(attr)):
            return None
        ax, ay, child = ctypes.c_int(), ctypes.c_int(), Window()
        if not xlib.XTranslateCoordinates(self.dpy, win, self.root, 0, 0,
                                          ctypes.byref(ax), ctypes.byref(ay), ctypes.byref(child)):
            return None
        return ax.value, ay.value, attr.width, attr.height, attr.map_state == IsViewable

    def offset_in(self, child, parent):
        ox, oy, dummy = ctypes.c_int(), ctypes.c_int(), Window()
        if not xlib.XTranslateCoordinates(self.dpy, child, parent, 0, 0,
                                          ctypes.byref(ox), ctypes.byref(oy), ctypes.byref(dummy)):
            return None
        return ox.value, oy.value

    def shape(self, win, kind):
        n, order = ctypes.c_int(), ctypes.c_int()
        p = xext.XShapeGetRectangles(self.dpy, win, kind, ctypes.byref(n), ctypes.byref(order))
        rects = []
        if p:
            rects = [(p[i].x, p[i].y, p[i].width, p[i].height) for i in range(n.value)
                     if p[i].width and p[i].height]
            xlib.XFree(p)
        return rects


# ---------------------------------------------------------------- the catcher window

class Catcher:
    """One invisible window covering one Bongo Cat window's visible pixels."""

    def __init__(self, x, target):
        self.x = x
        self.target = target
        self.win = None
        self.mapped = False
        self.shape_key = None
        self.waiting_until = 0.0     # after the mouse touched us: give Bongo Cat time to react
        self.cooldown_until = 0.0
        self.backoff = 1.0

    def _create(self):
        dpy = self.x.dpy
        vinfo = XVisualInfo()
        attrs = XSetWindowAttributes()
        attrs.override_redirect = 1
        attrs.event_mask = EnterWindowMask | PointerMotionMask | ButtonPressMask
        mask = CWOverrideRedirect | CWEventMask
        if xlib.XMatchVisualInfo(dpy, self.x.screen, 32, TrueColor, ctypes.byref(vinfo)):
            # 32-bit visual: fully transparent pixels, but still receives the mouse
            attrs.colormap = xlib.XCreateColormap(dpy, self.x.root, vinfo.visual, AllocNone)
            attrs.background_pixel = 0
            attrs.border_pixel = 0
            mask |= CWColormap | CWBackPixel | CWBorderPixel
            self.win = xlib.XCreateWindow(dpy, self.x.root, 0, 0, 1, 1, 0, 32, InputOutput,
                                          vinfo.visual, mask, ctypes.byref(attrs))
        else:
            self.win = xlib.XCreateWindow(dpy, self.x.root, 0, 0, 1, 1, 0, 0, InputOnly,
                                          None, mask, ctypes.byref(attrs))
        xlib.XStoreName(dpy, self.win, b"bongo-mousecatcher")

    def place(self, gx, gy, rects):
        """Cover exactly the given rects (relative to the target window at gx, gy)."""
        if self.win is None:
            self._create()
        x0 = min(r[0] for r in rects); y0 = min(r[1] for r in rects)
        x1 = max(r[0] + r[2] for r in rects); y1 = max(r[1] + r[3] for r in rects)
        key = (gx, gy, tuple(rects))
        if key == self.shape_key:
            return
        self.shape_key = key
        xlib.XMoveResizeWindow(self.x.dpy, self.win, gx + x0, gy + y0, x1 - x0, y1 - y0)
        arr = (XRectangle * len(rects))(*[XRectangle(r[0] - x0, r[1] - y0, r[2], r[3])
                                          for r in rects])
        for kind in (ShapeBounding, ShapeInput):
            xext.XShapeCombineRectangles(self.x.dpy, self.win, kind, 0, 0, arr, len(rects),
                                         ShapeSet, Unsorted)

    def show(self):
        if not self.mapped:
            xlib.XMapRaised(self.x.dpy, self.win)
            self.mapped = True

    def hide(self):
        if self.mapped:
            xlib.XUnmapWindow(self.x.dpy, self.win)
            self.mapped = False

    def destroy(self):
        if self.win is not None:
            xlib.XDestroyWindow(self.x.dpy, self.win)
            self.win = None

    def touched(self, now):
        """Mouse reached the cat. Stay put (so Bongo Cat can see the mouse) until
        Bongo Cat makes itself clickable - update() then steps aside."""
        if not self.waiting_until:
            self.waiting_until = now + 0.6

    def update(self, now, geo, bound, clickable):
        gx, gy, _, _, viewable = geo
        if not viewable or not bound:
            self.hide()
            return
        if clickable:                    # Bongo Cat noticed the mouse: job done
            self.hide()
            self.waiting_until = 0.0
            self.backoff = 1.0
            return
        if self.waiting_until:
            if now < self.waiting_until:
                return
            # Bongo Cat didn't become clickable (e.g. Gaming Mode is on): back off
            self.hide()
            self.waiting_until = 0.0
            self.cooldown_until = now + self.backoff
            self.backoff = min(self.backoff * 2, 30.0)
            return
        if now < self.cooldown_until:
            return
        self.place(gx, gy, bound)
        self.show()


# ---------------------------------------------------------------- main loop

def clip(rects, width, height):
    """Wine's shape mask can stick out past the window edge; keep only the part inside."""
    out = []
    for x, y, w, h in rects:
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + w, width), min(y + h, height)
        if x1 > x0 and y1 > y0:
            out.append((x0, y0, x1 - x0, y1 - y0))
    return out


def main():
    ap = argparse.ArgumentParser(description="Let Bongo Cat notice the mouse on Wayland.")
    ap.add_argument("--wm-class", default="steam_app_" + APPID,
                    help="WM_CLASS text identifying Bongo Cat's windows")
    ap.add_argument("--parent-pid", type=int, default=0)
    ap.add_argument("--test", action="store_true", help="only report what is found")
    args = ap.parse_args()

    if not xlib or not xext:
        log("libX11/libXext not found - mouse fix disabled")
        return 1
    _setup_prototypes()
    try:
        x = X()
    except RuntimeError as e:
        log("%s - mouse fix disabled" % e)
        return 1

    running = True

    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    log("mousecatcher started (looking for WM_CLASS '%s')" % args.wm_class)
    fd = xlib.XConnectionNumber(x.dpy)
    catchers = {}          # target window -> Catcher
    targets = []
    last_list = 0.0
    last_report = {}
    ev = XEvent()

    while running:
        now = time.monotonic()
        if args.parent_pid and not os.path.exists("/proc/%d" % args.parent_pid):
            log("launcher exited, stopping")
            break

        # handle mouse events on our catcher windows
        while xlib.XPending(x.dpy):
            xlib.XNextEvent(x.dpy, ctypes.byref(ev))
            if ev.type in (EnterNotify, MotionNotify, ButtonPress):
                for c in catchers.values():
                    if c.mapped:
                        c.touched(now)

        # find Bongo Cat's windows every 2 s
        if now - last_list > 2.0:
            last_list = now
            targets = [w for w in x.client_list() if args.wm_class in x.wm_class(w)]
            for w in list(catchers):
                if w not in targets:
                    catchers.pop(w).destroy()

        for w in targets:
            geo = x.geometry(w)
            if geo is None:
                continue
            _, _, width, height, viewable = geo
            bound = clip(x.shape(w, ShapeBounding), width, height)
            inp = clip(x.shape(w, ShapeInput), width, height)
            full = [(0, 0, width, height)]
            shaped = bool(bound) and bound != full
            if not shaped:
                # some Wine versions draw into a child window: look for its outline there
                for child in x.children(w):
                    off = x.offset_in(child, w)
                    cgeo = x.geometry(child)
                    if not off or not cgeo:
                        continue
                    crects = clip(x.shape(child, ShapeBounding), cgeo[2], cgeo[3])
                    if crects and crects != [(0, 0, cgeo[2], cgeo[3])]:
                        bound = clip([(r[0] + off[0], r[1] + off[1], r[2], r[3]) for r in crects],
                                     width, height)
                        shaped = bool(bound)
                        break
            clickable = bool(inp)
            state = (viewable, shaped, clickable)
            if last_report.get(w) != state:
                last_report[w] = state
                log("window 0x%x: %dx%d %s, %s, %s" % (
                    w, width, height, "visible" if viewable else "hidden",
                    "cat outline found (%d rects)" % len(bound) if shaped
                    else "no outline (turn on Transparency Fix / F3)",
                    "clickable" if clickable else "click-through"))
            if args.test:
                continue
            c = catchers.get(w)
            if c is None:
                c = catchers[w] = Catcher(x, w)
            c.update(now, geo, bound if shaped else None, clickable)

        xlib.XFlush(x.dpy)
        busy = any(c.waiting_until for c in catchers.values())
        select.select([fd], [], [], 0.02 if busy else 0.1)   # react fast while handing over

    for c in catchers.values():
        c.destroy()
    xlib.XSync(x.dpy, 0)
    xlib.XCloseDisplay(x.dpy)
    log("mousecatcher stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
