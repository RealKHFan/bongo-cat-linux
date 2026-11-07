#!/usr/bin/env python3
"""
bongo-diagnose.py - collects what's needed to debug Bongo Cat on Linux into
ONE text file you can share. It only reads files; it changes nothing.

    python3 bongo-diagnose.py

It collects: your distro and desktop, the bridge's settings and logs, Bongo
Cat's own game logs and saved settings, which Proton it uses and its launch
options. No keystrokes (the bridge never logs them). Your user name and Steam
ID are blanked out. Skim the file before sharing it.
"""
import glob
import hashlib
import os
import re
import subprocess
import sys
import time

APPID = "3419430"
HOME = os.path.expanduser("~")
USER = os.environ.get("USER") or os.path.basename(HOME)
BRIDGE = os.path.join(HOME, ".local", "share", "bongo-bridge")

KNOWN_BRIDGE_BUILDS = {
    "0951574a8324f2ee71a2fe89995147e0c1f7cd63026edf1700f5415837bc9d17":
        "Nov 6 2026 (paw-tap clicks, light 5 ms click fix)",
    "416d8eb39a06555d2a41c24b8f6eba6eef08845a817eca316003a9c663d91594":
        "Nov 5 2026 (paw-tap clicks, 5 ms click fix)",
    "2c67979ce20824a415d2ff2cf5d20f5183c0842d6e557b9a51ba5ab83d75c4ef":
        "Oct 2026 (pooled taps, real mouse clicks)",
    "4e2eb1ad3f7e39969f99188720408f7f7e0d6c184c1651ed1e7e2d44dbfb6ca9":
        "Sep 2026 (first GitHub version)",
}

out = []


def say(line=""):
    out.append(line)


def head(title):
    say()
    say("=" * 72)
    say(title)
    say("=" * 72)


def redact(text):
    text = text.replace(HOME, "~")
    if len(USER) >= 3:
        text = re.sub(re.escape(USER), "USER", text, flags=re.I)
    text = re.sub(r"7656119\d{10}", "STEAMID", text)
    # long opaque strings (tokens, keys) - keep the first few characters only
    text = re.sub(r"[A-Za-z0-9_\-+=]{48,}",
                  lambda m: m.group(0)[:6] + "...(hidden)", text)
    return text


def read(path, limit=None):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        data = f.read()
    return data if limit is None else data[-limit:]


def tail_lines(path, first=0, last=200):
    lines = read(path).splitlines()
    if len(lines) <= first + last:
        return lines
    return lines[:first] + ["... (%d lines skipped) ..." % (len(lines) - first - last)] + lines[-last:]


def dump_file(path, first=0, last=200):
    if not os.path.exists(path):
        say("  (missing) %s" % redact(path))
        return
    st = os.stat(path)
    say("--- %s  (%d bytes, changed %s)" % (
        redact(path), st.st_size, time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))))
    for line in tail_lines(path, first, last):
        say("  " + redact(line))


def listing(folder):
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        say("  (cannot list %s)" % redact(folder))
        return
    for n in names:
        p = os.path.join(folder, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        kind = "dir " if os.path.isdir(p) else "file"
        say("  %s %9d  %s  %s" % (kind, st.st_size,
            time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)), n))


def run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        return (r.stdout or r.stderr).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def section(fn):
    """Run one report section; a failure in one never stops the rest."""
    try:
        fn()
    except Exception as e:                       # noqa: BLE001
        say("  !! this section failed: %s: %s" % (type(e).__name__, e))


# ------------------------------------------------------------------ sections

def system():
    head("SYSTEM")
    pretty = ""
    try:
        for line in read("/etc/os-release").splitlines():
            if line.startswith("PRETTY_NAME="):
                pretty = line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    say("  distro:   %s" % pretty)
    say("  kernel:   %s" % os.uname().release)
    for var in ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "WAYLAND_DISPLAY", "DISPLAY"):
        say("  %-18s %s" % (var + ":", os.environ.get(var, "")))
    for cmd in (["plasmashell", "--version"], ["kwin_wayland", "--version"]):
        v = run(cmd)
        if v:
            say("  %-18s %s" % (cmd[0] + ":", v.splitlines()[0]))
    say("  groups:   %s" % run(["id", "-nG"]))
    say("  python:   %s" % sys.version.split()[0])


def kde():
    head("KDE SETTINGS")
    kwinrc = os.path.join(HOME, ".config", "kwinrc")
    if os.path.exists(kwinrc):
        group, found = None, 0
        for line in read(kwinrc).splitlines():
            if line.startswith("["):
                group = line
            if group == "[Xwayland]" or "Eavesdrop" in line:
                say("  kwinrc: " + line)
                found += 1
        if not found:
            say("  kwinrc: no [Xwayland] section (default 'listen to keystrokes' setting)")
    else:
        say("  no ~/.config/kwinrc")
    rules = os.path.join(HOME, ".config", "kwinrulesrc")
    if os.path.exists(rules):
        text = read(rules)
        say("  kwinrulesrc: %d rule(s); keep-cat-on-top rule present: %s" % (
            text.count("Description="), "Bongo Cat on top" in text))
        m = re.search(r"Bongo Cat on top.*?(?=\n\[|\Z)", text, re.S)
        if m:
            for line in m.group(0).splitlines():
                say("    " + line)


def screens():
    head("SCREENS (how KDE and X11 apps see them)")
    kd = run(["kscreen-doctor", "-o"])
    if kd:
        for line in re.sub(r"\x1b\[[0-9;]*m", "", kd).splitlines():
            if re.search(r"Output:|Geometry:|Scale:|enabled|primary|priority", line, re.I):
                say("  " + line.strip())
    xr = run(["xrandr", "--listmonitors"])
    if xr:
        for line in xr.splitlines():
            say("  xrandr: " + line.strip())
    if not kd and not xr:
        say("  (neither kscreen-doctor nor xrandr available)")


def input_devices():
    head("KEYBOARDS / MICE THE SYSTEM SEES")
    try:
        blocks = read("/proc/bus/input/devices").split("\n\n")
    except OSError:
        say("  cannot read /proc/bus/input/devices")
        return
    for b in blocks:
        name = re.search(r'N: Name="([^"]*)"', b)
        handlers = re.search(r"H: Handlers=(.*)", b)
        if name and handlers and ("kbd" in handlers.group(1) or "mouse" in handlers.group(1)):
            say("  %-45s %s" % (name.group(1)[:45], handlers.group(1).strip()))


def steam_roots():
    cands = [os.path.join(HOME, p) for p in (
        ".local/share/Steam", ".steam/steam", ".steam/root",
        ".var/app/com.valvesoftware.Steam/data/Steam",
        ".var/app/com.valvesoftware.Steam/.local/share/Steam")]
    seen, roots = set(), []
    for c in cands:
        if os.path.isdir(c):
            r = os.path.realpath(c)
            if r not in seen:
                seen.add(r)
                roots.append(r)
    return roots


def libraries(root):
    libs = [root]
    for vdf in (os.path.join(root, "steamapps", "libraryfolders.vdf"),
                os.path.join(root, "config", "libraryfolders.vdf")):
        if os.path.exists(vdf):
            for p in re.findall(r'"path"\s+"([^"]+)"', read(vdf)):
                p = p.replace("\\\\", "\\")
                if os.path.isdir(p) and os.path.realpath(p) not in map(os.path.realpath, libs):
                    libs.append(p)
    return libs


def vdf_block(text, start):
    """Text of the {...} block that starts at or after index `start`."""
    i = text.find("{", start)
    if i < 0:
        return ""
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    return text[i:]


GAME = {}     # filled by steam(): compat, pfx


def steam():
    head("STEAM / PROTON")
    roots = steam_roots()
    if not roots:
        say("  no Steam folder found")
        return
    for root in roots:
        say("  steam root: %s" % redact(root))
        for lib in libraries(root):
            acf = os.path.join(lib, "steamapps", "appmanifest_%s.acf" % APPID)
            if not os.path.exists(acf):
                continue
            text = read(acf)
            info = {k: (re.search(r'"%s"\s+"([^"]*)"' % k, text) or [None, "?"])[1]
                    for k in ("buildid", "LastUpdated", "StateFlags", "installdir")}
            try:
                updated = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(info["LastUpdated"])))
            except ValueError:
                updated = info["LastUpdated"]
            say("  Bongo Cat installed in library: %s" % redact(lib))
            say("    buildid %s, last updated %s, state %s" % (info["buildid"], updated, info["StateFlags"]))
            compat = os.path.join(lib, "steamapps", "compatdata", APPID)
            if os.path.isdir(compat):
                GAME["compat"] = compat
                GAME["pfx"] = os.path.join(compat, "pfx")
                say("    prefix: %s" % redact(compat))
                for f in ("version", "config_info"):
                    p = os.path.join(compat, f)
                    if os.path.exists(p):
                        say("    prefix %s: %s" % (f, redact(read(p).strip().splitlines()[0])))
            sibling = sorted(glob.glob(os.path.join(lib, "steamapps", "compatdata", APPID + "*")))
            if sibling:
                say("    prefix folders: " + ", ".join(os.path.basename(s) for s in sibling))
        cfg = os.path.join(root, "config", "config.vdf")
        if os.path.exists(cfg):
            text = read(cfg)
            i = text.find('"CompatToolMapping"')
            if i >= 0:
                block = vdf_block(text, i)
                j = block.find('"%s"' % APPID)
                if j >= 0:
                    m = re.search(r'"name"\s+"([^"]*)"', vdf_block(block, j))
                    say("  compatibility tool chosen: %s" % (m.group(1) if m and m.group(1) else "(Steam default)"))
                else:
                    say("  compatibility tool chosen: (none forced)")
        for lc in glob.glob(os.path.join(root, "userdata", "*", "config", "localconfig.vdf")):
            text = read(lc)
            for m in re.finditer(r'"%s"\s*\{' % APPID, text):
                lo = re.search(r'"LaunchOptions"\s+"((?:[^"\\]|\\.)*)"', vdf_block(text, m.start()))
                if lo:
                    say("  launch options: %s" % redact(lo.group(1)))
                    break
    compat_tools = []
    for root in roots:
        compat_tools += [os.path.basename(p) for p in glob.glob(os.path.join(root, "compatibilitytools.d", "*"))]
    if compat_tools:
        say("  custom Proton versions installed: " + ", ".join(sorted(compat_tools)))


def bridge():
    head("BRIDGE (~/.local/share/bongo-bridge)")
    if not os.path.isdir(BRIDGE):
        say("  not installed")
        return
    listing(BRIDGE)
    exe = os.path.join(BRIDGE, "keybridge.exe")
    if os.path.exists(exe):
        h = hashlib.sha256(open(exe, "rb").read()).hexdigest()
        say("  keybridge.exe sha256 %s = %s" % (h[:16], KNOWN_BRIDGE_BUILDS.get(h, "older or self-built")))
    for f in ("config.env", "LAUNCH-OPTIONS.txt"):
        dump_file(os.path.join(BRIDGE, f), last=60)
    for f in ("keybridge.log", "keyreader.log", "prelaunch.log", "mousecatcher.log"):
        dump_file(os.path.join(BRIDGE, f), first=10, last=120)


def game():
    head("BONGO CAT'S OWN FILES (inside its Proton prefix)")
    pfx = GAME.get("pfx")
    if not pfx or not os.path.isdir(pfx):
        say("  prefix not found - has Bongo Cat been started at least once?")
        return
    locallow = os.path.join(pfx, "drive_c", "users", "steamuser", "AppData", "LocalLow")
    folders = sorted({os.path.dirname(p) for p in glob.glob(os.path.join(locallow, "*", "*", "Player*.log"))})
    if not folders:
        say("  no Unity Player.log under %s" % redact(locallow))
        listing(locallow)
    for folder in folders:
        say("  game data folder: %s" % redact(folder))
        listing(folder)
        GAME.setdefault("company", os.path.basename(os.path.dirname(folder)))
        for name in sorted(os.listdir(folder)):
            p = os.path.join(folder, name)
            if not os.path.isfile(p):
                continue
            if name.startswith("Player") and name.endswith(".log"):
                dump_file(p, first=40, last=250)
            elif os.path.getsize(p) < 20000 and re.search(r"set|conf|pref|option|save", name, re.I) \
                    and not name.endswith((".png", ".jpg", ".dll")):
                dump_file(p, last=200)


def decode_hex(value):
    """Wine stores Unity PlayerPrefs strings as hex bytes; show them readable."""
    try:
        raw = bytes(int(b, 16) for b in value.replace("\\", "").split(",") if b.strip())
    except ValueError:
        return None
    raw = raw.rstrip(b"\x00")
    if raw and all(32 <= c < 127 for c in raw):
        return raw.decode()
    if len(raw) == 8:                       # doubles for float prefs
        import struct
        return "%g" % struct.unpack("<d", raw)[0]
    return None


def registry():
    head("SAVED SETTINGS IN THE PREFIX REGISTRY (user.reg)")
    pfx = GAME.get("pfx")
    reg = os.path.join(pfx or "", "user.reg")
    if not pfx or not os.path.exists(reg):
        say("  no user.reg")
        return
    company = GAME.get("company", "")
    text = read(reg).replace("\\\n  ", "")             # join continued hex lines
    sections = re.split(r"\n(?=\[)", text)
    shown = 0
    for sec in sections:
        header = sec.split("\n", 1)[0]
        h = header.lower()
        if "bongo" in h or (company and ("software\\\\" + company.lower()) in h):
            shown += 1
            say("  " + redact(header))
            for line in sec.splitlines()[1:]:
                m = re.match(r'(".*?")=hex(?:\(\w+\))?:(.*)', line)
                dec = decode_hex(m.group(2)) if m else None
                say("    " + redact(line[:160]) + ("   -> %s" % redact(dec) if dec is not None else ""))
    if not shown:
        say("  no Bongo Cat registry keys found (company folder: %r)" % company)


def proton_log():
    head("PROTON LOG (only exists if launched with PROTON_LOG=1)")
    p = os.path.join(HOME, "steam-%s.log" % APPID)
    if os.path.exists(p):
        dump_file(p, first=30, last=200)
    else:
        say("  none")


def processes():
    head("RUNNING RIGHT NOW")
    found = 0
    for p in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            cmd = open(p, "rb").read().replace(b"\x00", b" ").decode(errors="replace").strip()
        except OSError:
            continue
        if re.search(r"bongo|keybridge|keyreader|mousecatcher|" + APPID, cmd, re.I) \
                and "bongo-diagnose" not in cmd:
            found += 1
            say("  " + redact(cmd[:220]))
    if not found:
        say("  nothing Bongo Cat related is running")


def main():
    say("Bongo Cat on Linux - diagnostics, %s" % time.strftime("%Y-%m-%d %H:%M"))
    for fn in (system, kde, screens, input_devices, steam, bridge, game, registry, proton_log, processes):
        section(fn)
    desktop = os.path.join(HOME, "Desktop")
    target = os.path.join(desktop if os.path.isdir(desktop) else HOME, "bongo-diagnostics.txt")
    with open(target, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("Saved: %s" % target)
    print("It contains no keystrokes; your user name and Steam ID are blanked out.")
    print("Have a quick look, then attach it in the chat.")


if __name__ == "__main__":
    main()
