#!/usr/bin/env python3
"""
prelaunch.py - runs before every Bongo Cat start (bongo-launch.sh calls it).

Why: since the November 2026 update, Bongo Cat checks at startup whether the
previous session quit cleanly, and if not, it switches its Transparency Fix
off. On Linux the game is closed with Steam's "Stop" button, which always
counts as an unclean quit - so every start came up with a black box, an
invisible cat, or a cat that can't be clicked.

What it does: in Bongo Cat's saved settings (the Wine registry file user.reg
in its Proton prefix) it marks the last session as cleanly closed and, unless
you turned that off in config.env, switches the Transparency Fix back on.
Nothing else in the file is touched. It only runs while the prefix is idle.

    python3 prelaunch.py                    # normal use (prefix from Steam's env)
    python3 prelaunch.py --prefix PATH      # point it at a prefix by hand
    python3 prelaunch.py --dry-run          # show what would change
"""
import argparse
import os
import re
import sys
import time



def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def unity_name(key):
    """Unity stores PlayerPrefs as '<key>_h<djb2-xor hash of key>'."""
    h = 5381
    for c in key.encode():
        h = ((h * 33) ^ c) & 0xFFFFFFFF
    return "%s_h%d" % (key, h)


SESSION_OPEN = unity_name("WINDOW_SESSION_OPEN")      # 1 while the game runs
TRANSPARENCY_FIX = unity_name("Transparency Fix")     # the F3 setting


def wineserver_running(pfx):
    """True if a wineserver still has this prefix open. Editing user.reg then
    would be pointless: the server writes its own copy back when it exits.

    Wine versions keep their lock files in different places, so instead we look
    for a running wineserver process that holds the prefix directory open."""
    target = os.path.realpath(pfx)
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/comm" % pid) as f:
                if not f.read().startswith("wineserver"):
                    continue
            for fd in os.listdir("/proc/%s/fd" % pid):
                try:
                    if os.path.realpath(os.readlink("/proc/%s/fd/%s" % (pid, fd))) == target:
                        return True
                except OSError:
                    continue
        except OSError:
            continue                       # process ended, or not ours to inspect
    return False


def set_dword(lines, start, end, name, value):
    """Set "name"=dword:value inside lines[start:end]. Returns (lines, changed, old)."""
    want = '"%s"=dword:%08x' % (name, value)
    pattern = re.compile(r'^"%s"=' % re.escape(name))
    for i in range(start, end):
        if pattern.match(lines[i]):
            old = lines[i]
            if old == want:
                return lines, False, old
            lines[i] = want
            return lines, True, old
    # not there yet: add it after the header line and its #time line
    pos = start
    while pos < end and lines[pos].startswith("#"):
        pos += 1
    lines.insert(pos, want)
    return lines, True, None


def main():
    ap = argparse.ArgumentParser(description="Keep Bongo Cat's Transparency Fix on across Steam's Stop.")
    ap.add_argument("--prefix", help="the Proton prefix (default: $STEAM_COMPAT_DATA_PATH/pfx)")
    ap.add_argument("--transparency-fix", choices=("on", "keep"),
                    default=os.environ.get("BONGO_TRANSPARENCY_FIX") or "on",
                    help="on = switch it on every start (default); keep = only stop the reset")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pfx = args.prefix
    if not pfx:
        compat = os.environ.get("STEAM_COMPAT_DATA_PATH")
        if not compat:
            log("STEAM_COMPAT_DATA_PATH not set - not started from Steam? nothing done")
            return 0
        pfx = os.path.join(compat, "pfx")
    reg = os.path.join(pfx, "user.reg")
    if not os.path.isfile(reg):
        log("no user.reg yet (first start in a fresh prefix) - nothing to do")
        return 0

    for _ in range(30):                     # wait up to 15 s for a closing session
        if not wineserver_running(pfx):
            break
        time.sleep(0.5)
    else:
        log("Wine is still running in this prefix - skipped (settings would be overwritten)")
        return 0

    # latin-1 maps every byte 1:1, so the file is written back byte-for-byte
    with open(reg, "r", encoding="latin-1", newline="") as f:
        text = f.read()
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)

    start = None
    for i, l in enumerate(lines):
        if l.startswith("[") and l.lower().split("]")[0].endswith("\\\\bongocat"):
            start = i
            break
    if start is None:
        log("Bongo Cat hasn't saved settings yet - nothing to do")
        return 0
    end = start + 1
    while end < len(lines) and not lines[end].startswith("["):
        end += 1
    while end > start + 1 and lines[end - 1] == "":
        end -= 1                             # keep the blank line between sections

    changes = []
    lines, changed, old = set_dword(lines, start + 1, end, SESSION_OPEN, 0)
    if changed:
        changes.append("marked last session as closed cleanly (was %s)" % (old.split("=")[-1] if old else "unset"))
        end += 0 if old else 1
    if args.transparency_fix == "on":
        lines, changed, old = set_dword(lines, start + 1, end, TRANSPARENCY_FIX, 1)
        if changed:
            changes.append("Transparency Fix switched on (was %s)" % (old.split("=")[-1] if old else "unset"))

    if not changes:
        log("settings already fine - nothing to change")
        return 0
    if args.dry_run:
        for c in changes:
            log("would do: " + c)
        return 0

    backup = reg + ".before-bongo-bridge"
    if not os.path.exists(backup):           # one-time safety copy of the original
        with open(backup, "w", encoding="latin-1", newline="") as f:
            f.write(text)
    tmp = reg + ".bongo-tmp"
    with open(tmp, "w", encoding="latin-1", newline="") as f:
        f.write(nl.join(lines))
    os.chmod(tmp, os.stat(reg).st_mode & 0o7777)
    os.replace(tmp, reg)
    for c in changes:
        log(c)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                   # never block the game from starting
        log("prelaunch failed, game starts anyway: %s: %s" % (type(e).__name__, e))
        sys.exit(0)
