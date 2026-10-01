#!/usr/bin/env python3
"""
keyreader.py - Bongo Cat key bridge, Linux side.

Reads key presses straight from /dev/input (your user must be in the 'input'
group), turns each press into an anonymous paw tap and sends it to
keybridge.exe, which runs inside Bongo Cat's Proton prefix and replays it so
Bongo Cat reacts while you type in other apps.

Only WHICH HALF of the keyboard you hit is forwarded, never the actual key.
Each key that is down gets its own letter from that side's pool, so pressing
30 keys at once counts as 30 taps instead of 2.

No extra Python packages needed (pure standard library).

    python3 keyreader.py --test      # show detected keyboards + live taps, no Bongo Cat needed
"""
import argparse
import collections
import errno
import os
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time

EV_KEY = 0x01
EV_REL = 0x02
EVENT_FMT = "llHHi"                     # struct input_event (timeval, type, code, value)
EVENT_SIZE = struct.calcsize(EVENT_FMT)
LONG_BITS = struct.calcsize("l") * 8

KEY_A, KEY_Z, KEY_SPACE = 30, 44, 57
BTN_LEFT, BTN_TASK = 0x110, 0x117       # mouse buttons 272..279

# Linux key codes on the LEFT half of a normal keyboard (everything else = right)
LEFT_KEYS = {
    1,                      # Esc
    41, 2, 3, 4, 5, 6,      # ` 1 2 3 4 5
    15, 16, 17, 18, 19, 20,  # Tab Q W E R T
    58, 30, 31, 32, 33, 34,  # Caps A S D F G
    42, 86, 44, 45, 46, 47, 48,  # LShift <> Z X C V B
    29, 125, 56,            # LCtrl LMeta LAlt
    59, 60, 61, 62, 63, 64,  # F1..F6
}

# Letters each side of the keyboard taps with. Between them they use A-Z once
# each, so up to 26 keys can be down at the same time and still be counted
# separately. The letter is just the next free one in the pool - it carries no
# information about which key you actually pressed.
LEFT_POOL = "QWERTASDFGZXCV"
RIGHT_POOL = "YUIOPHJKLBNM"

# Marks a held mouse button internally. Deliberately not a single letter, so
# it can't be confused with the tap slot "M", which is a normal right-hand key.
MOUSE_TOKEN = "mouse"


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def notify(msg):
    """Desktop popup so problems aren't hidden in a log file."""
    if shutil.which("notify-send"):
        try:
            subprocess.Popen(["notify-send", "-a", "Bongo Cat bridge", "Bongo Cat bridge", msg],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


# ---------------------------------------------------------------- device discovery

def _bitmap(text):
    val = 0
    for word in text.split():
        val = (val << LONG_BITS) | int(word, 16)
    return val


def find_devices(want_mouse, proc_path="/proc/bus/input/devices"):
    """Return {'/dev/input/eventN': ('keyboard'|'mouse', name)}."""
    found = {}
    try:
        with open(proc_path) as f:
            blocks = f.read().split("\n\n")
    except OSError:
        return found
    for block in blocks:
        name, handlers, ev, keys = "", [], 0, 0
        for line in block.splitlines():
            if line.startswith("N: Name="):
                name = line[8:].strip().strip('"')
            elif line.startswith("H: Handlers="):
                handlers = line[12:].split()
            elif line.startswith("B: EV="):
                ev = int(line[6:].strip(), 16)
            elif line.startswith("B: KEY="):
                keys = _bitmap(line[7:])
        event = next((h for h in handlers if h.startswith("event")), None)
        if not event or not (ev >> EV_KEY) & 1:
            continue
        path = "/dev/input/" + event
        has_letters = all((keys >> k) & 1 for k in (KEY_A, KEY_Z, KEY_SPACE))
        has_buttons = (keys >> BTN_LEFT) & 1 and (ev >> EV_REL) & 1
        if has_letters:
            found[path] = ("keyboard", name)
        elif want_mouse and has_buttons:
            found[path] = ("mouse", name)
    return found


# ---------------------------------------------------------------- tap logic

class Paws:
    """Turns physical presses into anonymous tap bytes.

    Every key that is down at the same time gets its OWN letter out of the
    pool for its side of the keyboard. That matters: Bongo Cat counts a tap
    per key, so if everything mapped to one letter per side (as this used to
    do), mashing 30 keys at once would only ever count as 2 taps.

    Which letter you get says nothing about which key you pressed - it is
    just the next free one in the pool - so only the side of the keyboard
    ever leaves this machine.
    """

    def __init__(self, send, mouse_mode="paw"):
        self.send = send
        self.mouse_mode = mouse_mode          # "off" | "paw" | "real"
        self.free = {"L": collections.deque(LEFT_POOL),
                     "R": collections.deque(RIGHT_POOL)}
        self.active = {}                      # (device, code) -> letter, or "M"
        self.held = {}                        # letter -> how many keys share it
        self.space_toggle = False
        self.mouse_toggle = False

    @staticmethod
    def side_of(letter):
        return "L" if letter in LEFT_POOL else "R"

    def side_for(self, code):
        if code == KEY_SPACE:                 # space alternates, like real bongoing
            self.space_toggle = not self.space_toggle
            return "L" if self.space_toggle else "R"
        return "L" if code in LEFT_KEYS else "R"

    def take(self, side):
        """Next free letter: own side first, then the other side, then share."""
        for s in (side, "R" if side == "L" else "L"):
            if self.free[s]:
                return self.free[s].popleft()
        # 27+ keys held down at once: share a letter rather than drop the tap
        return (LEFT_POOL if side == "L" else RIGHT_POOL)[0]

    def press(self, dev, kind, code):
        key = (dev, code)
        if key in self.active:
            return
        is_mouse = kind == "mouse" or BTN_LEFT <= code <= BTN_TASK
        if is_mouse:
            if self.mouse_mode == "off":
                return
            if self.mouse_mode == "real":
                self.active[key] = MOUSE_TOKEN
                self.held[MOUSE_TOKEN] = self.held.get(MOUSE_TOKEN, 0) + 1
                self.send("1")
                return
            # "paw": a click taps a paw, alternating sides
            self.mouse_toggle = not self.mouse_toggle
            side = "L" if self.mouse_toggle else "R"
        else:
            side = self.side_for(code)
        letter = self.take(side)
        self.active[key] = letter
        self.held[letter] = self.held.get(letter, 0) + 1
        self.send(letter)                     # uppercase = tap

    def release(self, dev, code):
        letter = self.active.pop((dev, code), None)
        if letter is None:
            return
        self.held[letter] = self.held.get(letter, 1) - 1
        if self.held[letter] > 0:
            return
        del self.held[letter]
        if letter == MOUSE_TOKEN:
            self.send("0")
            return
        self.send(letter.lower())
        self.free[self.side_of(letter)].append(letter)   # back of the queue, so it rests a while

    def drop_device(self, dev):
        for key in [k for k in self.active if k[0] == dev]:
            self.release(*key)

    def reset(self):
        self.active.clear()
        self.held.clear()
        self.free = {"L": collections.deque(LEFT_POOL),
                     "R": collections.deque(RIGHT_POOL)}


# ---------------------------------------------------------------- connection

class Link:
    def __init__(self, port, test_mode):
        self.port = port
        self.test = test_mode
        self.sock = None
        self.next_try = 0.0
        self.on_reset = None

    def ensure(self):
        if self.test or self.sock or time.monotonic() < self.next_try:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", self.port))
        except OSError:
            s.close()
            self.next_try = time.monotonic() + 1.0
            return
        s.setblocking(False)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = s
        log("connected to keybridge.exe on 127.0.0.1:%d" % self.port)
        if self.on_reset:
            self.on_reset()

    def send(self, ch):
        if self.test:
            if ch.isupper():
                what = "mouse click" if ch == "M" else (
                    "left paw " if ch in LEFT_POOL else "right paw") + "  (slot %s)" % ch
                log("tap  %s" % what)
            return
        if not self.sock:
            return
        try:
            self.sock.send(ch.encode())
        except OSError:
            self.drop()

    def drop(self):
        if self.sock:
            log("lost connection to keybridge.exe, will retry")
            self.sock.close()
            self.sock = None
            self.next_try = time.monotonic() + 1.0
            if self.on_reset:
                self.on_reset()   # reset held state


# ---------------------------------------------------------------- main loop

def main():
    ap = argparse.ArgumentParser(description="Forward typing to Bongo Cat running under Proton.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("BRIDGE_PORT", 47811)))
    ap.add_argument("--mouse-mode", choices=("off", "paw", "real"),
                    default=os.environ.get("BONGO_MOUSE_MODE", "paw"),
                    help="off = ignore clicks, paw = a click taps a paw, "
                         "real = replay it as an actual mouse click")
    ap.add_argument("--mouse", action="store_true",
                    help=argparse.SUPPRESS)      # old spelling of --mouse-mode paw
    ap.add_argument("--parent-pid", type=int, default=0, help="exit when this process exits")
    ap.add_argument("--test", action="store_true", help="print taps instead of sending them")
    args = ap.parse_args()
    mouse_mode = args.mouse_mode
    if args.mouse and mouse_mode == "off":
        mouse_mode = "paw"
    want_mouse = mouse_mode != "off"

    running = True

    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    link = Link(args.port, args.test)
    paws = Paws(link.send, mouse_mode)
    link.on_reset = paws.reset

    fds = {}            # fd -> (path, kind)
    last_scan = 0.0
    warned = False

    log("keyreader started (port %d, mouse=%s, test=%s)" % (args.port, mouse_mode, args.test))

    while running:
        now = time.monotonic()

        if args.parent_pid and not os.path.exists("/proc/%d" % args.parent_pid):
            log("launcher exited, stopping")
            break

        # (re)scan for keyboards every 3 s - handles hot-plugging
        if now - last_scan > 3.0:
            last_scan = now
            wanted = find_devices(want_mouse)
            open_paths = {p for p, _ in fds.values()}
            denied = []
            for path, (kind, name) in wanted.items():
                if path in open_paths:
                    continue
                try:
                    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                except PermissionError:
                    denied.append(name)
                    continue
                except OSError:
                    continue
                fds[fd] = (path, kind)
                log("listening to %s: %s (%s)" % (kind, name, path))
            if denied and not fds and not warned:
                warned = True
                msg = ("Can't read your keyboard (permission denied). Run install.sh again to "
                       "join the 'input' group, then log out and back in.")
                log(msg)
                notify(msg)
            if not wanted and not warned:
                warned = True
                log("no keyboards found in /proc/bus/input/devices")

        link.ensure()

        if not fds:
            time.sleep(0.5)
            continue

        try:
            ready, _, _ = select.select(list(fds), [], [], 0.5)
        except (OSError, ValueError):
            ready = []

        for fd in ready:
            path, kind = fds[fd]
            try:
                data = os.read(fd, EVENT_SIZE * 64)
            except OSError as e:
                if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    continue
                log("device gone: %s" % path)
                paws.drop_device(path)
                os.close(fd)
                del fds[fd]
                continue
            for off in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                _, _, etype, code, value = struct.unpack_from(EVENT_FMT, data, off)
                if etype != EV_KEY:
                    continue
                if kind == "mouse" and not (BTN_LEFT <= code <= BTN_TASK):
                    continue
                if value == 1:
                    paws.press(path, kind, code)
                elif value == 0:
                    paws.release(path, code)
                # value == 2 is auto-repeat: ignored

    # clean shutdown: let go of anything still held
    for key in list(paws.active):
        paws.release(*key)
    for fd in fds:
        os.close(fd)
    if link.sock:
        link.sock.close()
    log("keyreader stopped")


if __name__ == "__main__":
    sys.exit(main())
