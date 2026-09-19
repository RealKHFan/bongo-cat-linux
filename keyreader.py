#!/usr/bin/env python3
"""
keyreader.py - Bongo Cat key bridge, Linux side.

Reads key presses straight from /dev/input (your user must be in the 'input'
group), turns each press into an anonymous "left paw" / "right paw" tap and
sends it to keybridge.exe, which runs inside Bongo Cat's Proton prefix and
replays it so Bongo Cat reacts while you type in other apps.

Only WHICH HALF of the keyboard you hit is forwarded, never the actual key:
left half -> F, right half -> J, space alternates, mouse clicks -> K.

No extra Python packages needed (pure standard library).

    python3 keyreader.py --test      # show detected keyboards + live taps, no Bongo Cat needed
"""
import argparse
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

LETTER_LEFT, LETTER_RIGHT, LETTER_MOUSE = "F", "J", "K"


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
    """Turns physical presses into letter down/up bytes; every press is a fresh tap."""

    def __init__(self, send):
        self.send = send
        self.held = {LETTER_LEFT: 0, LETTER_RIGHT: 0, LETTER_MOUSE: 0}
        self.active = {}          # (device, code) -> letter it was mapped to
        self.space_toggle = False

    def letter_for(self, kind, code):
        if kind == "mouse" or BTN_LEFT <= code <= BTN_TASK:
            return LETTER_MOUSE
        if code == KEY_SPACE:
            self.space_toggle = not self.space_toggle
            return LETTER_LEFT if self.space_toggle else LETTER_RIGHT
        return LETTER_LEFT if code in LEFT_KEYS else LETTER_RIGHT

    def press(self, dev, kind, code):
        if (dev, code) in self.active:
            return
        letter = self.letter_for(kind, code)
        self.active[(dev, code)] = letter
        self.held[letter] += 1
        self.send(letter.upper())   # keybridge re-taps if the letter is already down

    def release(self, dev, code):
        letter = self.active.pop((dev, code), None)
        if letter is None:
            return
        self.held[letter] -= 1
        if self.held[letter] == 0:
            self.send(letter.lower())

    def drop_device(self, dev):
        for key in [k for k in self.active if k[0] == dev]:
            self.release(*key)

    def reset(self):
        self.active.clear()
        for letter in self.held:
            self.held[letter] = 0


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
            arrow = "down" if ch.isupper() else "up  "
            log("tap %s %s" % (arrow, {"F": "left paw", "J": "right paw", "K": "mouse"}[ch.upper()]))
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
    ap.add_argument("--mouse", action="store_true", help="count mouse clicks as taps too")
    ap.add_argument("--parent-pid", type=int, default=0, help="exit when this process exits")
    ap.add_argument("--test", action="store_true", help="print taps instead of sending them")
    args = ap.parse_args()

    running = True

    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    link = Link(args.port, args.test)
    paws = Paws(link.send)
    link.on_reset = paws.reset

    fds = {}            # fd -> (path, kind)
    last_scan = 0.0
    warned = False

    log("keyreader started (port %d, mouse=%s, test=%s)" % (args.port, args.mouse, args.test))

    while running:
        now = time.monotonic()

        if args.parent_pid and not os.path.exists("/proc/%d" % args.parent_pid):
            log("launcher exited, stopping")
            break

        # (re)scan for keyboards every 3 s - handles hot-plugging
        if now - last_scan > 3.0:
            last_scan = now
            wanted = find_devices(args.mouse)
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
