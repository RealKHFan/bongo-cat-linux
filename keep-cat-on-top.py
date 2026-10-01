#!/usr/bin/env python3
"""
keep-cat-on-top.py - stop Bongo Cat disappearing behind other windows (KDE only).

Wine already marks the cat "always on top", but so do plenty of other windows,
and the panel outranks it anyway, so the cat still gets covered. KWin can put a
window in a higher stacking layer, which settles it for good.

This writes one KWin window rule for Bongo Cat and reloads KWin. It touches
nothing else on your desktop, and your existing rules are backed up first.

    python3 keep-cat-on-top.py              # above normal windows and the panel
    python3 keep-cat-on-top.py --fullscreen # above fullscreen windows as well
    python3 keep-cat-on-top.py --remove     # undo
    python3 keep-cat-on-top.py --dry-run    # show what would change

Doing it by hand instead: System Settings > Window Management > Window Rules >
Add New, window class Exact Match "steam_app_3419430", Add Property > Layer,
set it to Force + Notification.
"""
import argparse
import configparser
import os
import shutil
import subprocess
import sys
import time
import uuid

RULES_FILE = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "kwinrulesrc")

WM_CLASS = "steam_app_3419430"
DESCRIPTION = "Bongo Cat on top (bongo-cat-linux)"

# KWin stacking layers, lowest first:
#   desktop below normal above notification fullscreen popup
#   critical-notification osd overlay
LAYER_NORMAL = "notification"            # over every ordinary window and the panel
LAYER_FULLSCREEN = "critical-notification"   # also over fullscreen windows

FORCE = "2"          # KWin rule type: 0 unused, 1 don't affect, 2 force, 3 apply
EXACT_MATCH = "1"    # KWin string match: 0 unimportant, 1 exact, 2 substring, 3 regex


def load():
    """KWin's rule file is INI, but keys are case sensitive, unlike the default."""
    cp = configparser.RawConfigParser()
    cp.optionxform = str
    if os.path.exists(RULES_FILE):
        cp.read(RULES_FILE, encoding="utf-8")
    return cp


def rule_ids(cp):
    if not cp.has_section("General"):
        return []
    raw = cp.get("General", "rules", fallback="")
    return [r for r in raw.split(",") if r]


def ours(cp):
    return [r for r in rule_ids(cp)
            if cp.has_section(r) and cp.get(r, "Description", fallback="") == DESCRIPTION]


def write(cp, dry_run):
    if dry_run:
        print("--- would write to %s:" % RULES_FILE)
        cp.write(sys.stdout, space_around_delimiters=False)
        return True
    os.makedirs(os.path.dirname(RULES_FILE), exist_ok=True)
    if os.path.exists(RULES_FILE):
        backup = "%s.bak-%s" % (RULES_FILE, time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(RULES_FILE, backup)
        print("   backed up your existing rules to %s" % backup)
    tmp = RULES_FILE + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        cp.write(f, space_around_delimiters=False)
    os.replace(tmp, RULES_FILE)
    return True


def reload_kwin():
    """Ask KWin to re-read its config. Harmless if it isn't running."""
    for cmd in (["qdbus6", "org.kde.KWin", "/KWin", "reconfigure"],
                ["qdbus", "org.kde.KWin", "/KWin", "reconfigure"],
                ["dbus-send", "--session", "--dest=org.kde.KWin", "--type=method_call",
                 "/KWin", "org.kde.KWin.reconfigure"]):
        if not shutil.which(cmd[0]):
            continue
        try:
            if subprocess.run(cmd, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=10).returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def main():
    ap = argparse.ArgumentParser(
        description="Keep Bongo Cat above other windows on KDE Plasma 6.")
    ap.add_argument("--fullscreen", action="store_true",
                    help="also keep it above fullscreen windows")
    ap.add_argument("--remove", action="store_true", help="remove the rule again")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the resulting config instead of saving it")
    args = ap.parse_args()

    if not args.dry_run and not os.environ.get("KDE_FULL_SESSION") \
            and "kde" not in os.environ.get("XDG_CURRENT_DESKTOP", "").lower():
        print("This only works on KDE Plasma - KWin is what stacks the windows.")
        print("On GNOME or another desktop, look for an \"always on top\" or")
        print("\"window rules\" extension instead.")
        return 1

    cp = load()
    if not cp.has_section("General"):
        cp.add_section("General")

    existing = ours(cp)
    keep = [r for r in rule_ids(cp) if r not in existing]
    for r in existing:
        cp.remove_section(r)

    if args.remove:
        if not existing:
            print("No rule of ours found - nothing to remove.")
            return 0
        cp.set("General", "rules", ",".join(keep))
        cp.set("General", "count", str(len(keep)))
        write(cp, args.dry_run)
        if not args.dry_run:
            print("   rule removed")
            reload_kwin()
        return 0

    layer = LAYER_FULLSCREEN if args.fullscreen else LAYER_NORMAL
    rid = "{%s}" % uuid.uuid4()
    cp.add_section(rid)
    cp.set(rid, "Description", DESCRIPTION)
    cp.set(rid, "wmclass", WM_CLASS)
    cp.set(rid, "wmclassmatch", EXACT_MATCH)
    cp.set(rid, "wmclasscomplete", "false")
    cp.set(rid, "layer", layer)
    cp.set(rid, "layerrule", FORCE)

    keep.append(rid)
    cp.set("General", "rules", ",".join(keep))
    cp.set("General", "count", str(len(keep)))

    if not write(cp, args.dry_run):
        return 1
    if args.dry_run:
        return 0

    if existing:
        print("   replaced the rule this script added before")
    print("   rule added: Bongo Cat -> layer '%s'" % layer)
    if reload_kwin():
        print("   KWin reloaded - the cat should stay on top from now on")
    else:
        print("   couldn't reach KWin to reload it; log out and back in, or run:")
        print("     qdbus org.kde.KWin /KWin reconfigure")
    print()
    print("   Undo at any time with:  python3 %s --remove" % os.path.basename(__file__))
    return 0


if __name__ == "__main__":
    sys.exit(main())
