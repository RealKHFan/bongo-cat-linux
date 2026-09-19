#!/usr/bin/env bash
# Bongo Cat on Linux - installer. Also use it to update: your config.env is kept.
# Run it from the downloaded folder:   bash install.sh
set -uo pipefail

SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DEST="$HOME/.local/share/bongo-bridge"
NEED_RELOGIN=0

step() { printf '\n\033[1;36m[%s] %s\033[0m\n' "$1" "$2"; }
ok()   { printf '   \033[1;32mOK\033[0m  %s\n' "$*"; }
warn() { printf '   \033[1;33m!!\033[0m  %s\n' "$*"; }
die()  { printf '\n\033[1;31mERROR:\033[0m %s\n' "$*"; exit 1; }
ask()  { local a; read -rp "   $1 [Y/n] " a; [[ ! "$a" =~ ^[Nn] ]]; }

[ "$(id -u)" -eq 0 ] && die "Run this as your normal user, not with sudo. It will ask for your password when needed."

# ---------------------------------------------------------------------------
step 1/4 "Copying the bridge to $DEST"
for f in bongo-launch.sh keyreader.py mousecatcher.py keybridge.c config.env uninstall.sh build-helper.sh README.md; do
    [ -f "$SRC/$f" ] || die "Missing $f. Download the whole repository and run install.sh from inside that folder."
done
[ -f "$SRC/keybridge.exe" ] || die "Missing keybridge.exe. Build it first with:  bash build-helper.sh"
mkdir -p "$DEST"
install -m 755 "$SRC/bongo-launch.sh" "$SRC/keyreader.py" "$SRC/mousecatcher.py" "$SRC/uninstall.sh" "$SRC/build-helper.sh" "$DEST/"
install -m 644 "$SRC/keybridge.exe" "$SRC/keybridge.c" "$SRC/README.md" "$DEST/"
if [ -f "$DEST/config.env" ]; then
    ok "kept your existing config.env"
    if ! grep -q '^CLICK_MODE=' "$DEST/config.env"; then
        cat >> "$DEST/config.env" <<'CFG'

# How the cat becomes clickable on Wayland (needs Bongo Cat's Transparency Fix, F3):
#   always = the cat can always be clicked, the see-through area passes clicks on (default)
#   hover  = like on Windows: only while the mouse is over it. Respects Gaming Mode,
#            but doesn't work during Bongo Cat's first-time setup
#   off    = don't help
CLICK_MODE=always
CFG
        ok "added the new CLICK_MODE setting to your config.env"
    fi
else
    install -m 644 "$SRC/config.env" "$DEST/"
fi
ok "files installed"
if command -v python3 >/dev/null 2>&1; then
    ok "python3 found"
else
    warn "python3 not found - install it with your package manager, or the typing bridge won't run"
fi

# ---------------------------------------------------------------------------
step 2/4 "Keyboard access for the typing bridge ('input' group)"
if id -nG | grep -qw input; then
    ok "you're already in the 'input' group"
elif id -nG "$USER" | grep -qw input; then
    ok "you were added to 'input' already"
    warn "it only takes effect after you log out and back in (or reboot)"
    NEED_RELOGIN=1
else
    echo "   The bridge reads your keyboard directly, which needs the 'input' group."
    echo "   Note: any program running as you can then read keystrokes too."
    if ask "Add $USER to the 'input' group now? (asks for your password)"; then
        # Fedora Atomic distros (Bazzite, Bluefin, Silverblue...) keep system
        # groups in /usr/lib/group; copy the entry to /etc/group first or
        # usermod can't find it. Other distros already have it in /etc/group.
        if ! grep -q '^input:' /etc/group; then
            if grep -q '^input:' /usr/lib/group 2>/dev/null; then
                grep -E '^input:' /usr/lib/group | sudo tee -a /etc/group >/dev/null \
                    || die "Couldn't update /etc/group"
            else
                die "No 'input' group found on this system."
            fi
        fi
        sudo usermod -aG input "$USER" \
            || die "usermod failed. On a Steam Deck, set a password first with:  passwd"
        ok "added - log out and back in (or reboot) before playing"
        NEED_RELOGIN=1
    else
        warn "skipped - the cat will only react while its own window is focused"
    fi
fi

# ---------------------------------------------------------------------------
step 3/4 "Checking which Steam you use"
FLATPAK_STEAM="$HOME/.var/app/com.valvesoftware.Steam"
if [ -d "$HOME/.steam/steam/steamapps" ] || [ -d "$HOME/.local/share/Steam/steamapps" ]; then
    ok "normal (non-Flatpak) Steam"
elif [ -d "$FLATPAK_STEAM" ] && command -v flatpak >/dev/null 2>&1; then
    warn "Flatpak Steam found. It can't see $DEST unless you allow it (Flatpak support is untested)."
    if ask "Allow Flatpak Steam to use the bridge folder?"; then
        flatpak override --user --filesystem="$DEST" com.valvesoftware.Steam \
            && ok "done - restart Steam before launching Bongo Cat" \
            || warn "flatpak override failed"
    fi
else
    warn "couldn't find your Steam folder - start Steam once if you haven't yet"
fi

# ---------------------------------------------------------------------------
step 4/4 "Your Steam launch options"
launcher="$DEST/bongo-launch.sh"
[[ "$launcher" == *" "* ]] && launcher="\"$launcher\""
line="$launcher %command%"
echo
echo "   Paste this into Bongo Cat > Properties > General > Launch Options"
echo "   (replace anything that's already there):"
echo
printf '      \033[1m%s\033[0m\n' "$line"
echo
if command -v wl-copy >/dev/null 2>&1 && printf '%s' "$line" | wl-copy 2>/dev/null; then
    ok "also copied to your clipboard"
elif command -v xclip >/dev/null 2>&1 && printf '%s' "$line" | xclip -selection clipboard 2>/dev/null; then
    ok "also copied to your clipboard"
fi
printf '%s\n' "$line" > "$DEST/LAUNCH-OPTIONS.txt"
ok "saved to $DEST/LAUNCH-OPTIONS.txt in case you lose it"

echo
echo "   What's left:"
n=1
if [ "$NEED_RELOGIN" = 1 ]; then
    echo "   $n. Log out and back in (or reboot)."; n=$((n+1))
fi
echo "   $n. Bongo Cat > Properties > Compatibility > force Proton Experimental."; n=$((n+1))
echo "   $n. Paste the launch options above (skip if they're already set)."; n=$((n+1))
echo "   $n. Launch Bongo Cat and press F3 as soon as the cat appears (see README.md)."
echo
