#!/usr/bin/env bash
# Bongo Cat on Linux - Steam launch wrapper.
#
# Put this in Bongo Cat's Steam launch options (install.sh prints the exact line):
#     /home/YOURNAME/.local/share/bongo-bridge/bongo-launch.sh %command%
#
# It starts helpers, then the game:
#   - the key bridge, so Bongo Cat reacts while you type in other apps
#   - the click fix, so the cat can be clicked on Wayland
# (plus GE-Proton's transparency switch, only if you enable it in config.env).
# Settings live in config.env next to this file.

BRIDGE_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
[ -f "$BRIDGE_DIR/config.env" ] && . "$BRIDGE_DIR/config.env"

: "${TRANSPARENCY_MODE:=off}"
: "${KEY_BRIDGE:=1}"
# CLICK_MODE: always (default) / hover / off. Older configs only have MOUSE_FIX.
if [ -z "${CLICK_MODE:-}" ]; then
    if [ "${MOUSE_FIX:-1}" = "0" ]; then CLICK_MODE=off; else CLICK_MODE=always; fi
fi
# MOUSE_CLICKS: real (default) / paw / off. Old configs used 1 and 0.
case "${MOUSE_CLICKS:-real}" in
    1) MOUSE_CLICKS=paw ;;
    0) MOUSE_CLICKS=off ;;
    real|paw|off) ;;
    *) MOUSE_CLICKS=real ;;
esac
: "${BRIDGE_PORT:=47811}"

notify() {
    command -v notify-send >/dev/null 2>&1 && notify-send -a "Bongo Cat bridge" "Bongo Cat bridge" "$1"
    echo "[bongo-launch] $1" >&2
}

# --- 1. Transparency -------------------------------------------------------
# The overlay patch lives in Wine's X11 driver, so make sure Wine-Wayland is off.
unset PROTON_ENABLE_WAYLAND
case "$TRANSPARENCY_MODE" in
    alpha) export WINE_LAYERED_OVERLAY_ALPHA=1 ;;   # true per-pixel alpha (best)
    shape) export WINE_LAYERED_OVERLAY_SHAPE=1 ;;   # cut-out fallback
    off)   ;;
    *)     notify "Unknown TRANSPARENCY_MODE '$TRANSPARENCY_MODE' in config.env (use alpha, shape or off)" ;;
esac

# Warn if Steam isn't actually using a new-enough GE-Proton. The version is read
# from the Proton folder's "version" file, so renamed folders like
# "GE-Proton Latest" (ProtonPlus) are recognised too.
if [ "$TRANSPARENCY_MODE" != "off" ]; then
    proton_dir=""
    for a in "$@"; do
        case "$a" in */proton) proton_dir="$(dirname "$a")" ;; esac
    done
    if [ -n "$proton_dir" ]; then
        ver_text="$(cat "$proton_dir/version" 2>/dev/null) $proton_dir"
        if [[ "$ver_text" =~ GE-Proton([0-9]+)-([0-9]+) ]]; then
            major=${BASH_REMATCH[1]}; minor=${BASH_REMATCH[2]}
            if (( major < 11 || (major == 11 && minor < 6) )); then
                notify "Bongo Cat is using GE-Proton$major-$minor. Transparency needs GE-Proton11-6 or newer."
            fi
        elif [[ "$ver_text" != *GE* ]]; then
            notify "Bongo Cat isn't running on GE-Proton, so the background will stay black. Pick GE-Proton in Properties > Compatibility."
        fi
    fi
fi

# Our Python helpers must not inherit Steam's overlay/runtime libraries.
helper() { ( unset LD_PRELOAD LD_LIBRARY_PATH; exec python3 "$@" ); }

# --- 2. Key bridge + click fix (keybridge.exe runs inside Bongo Cat's prefix) --
READER_PID=""
if [ "$KEY_BRIDGE" = "1" ] || [ "$CLICK_MODE" = "always" ]; then
    if [ -f "$BRIDGE_DIR/keybridge.exe" ]; then
        export BONGO_BRIDGE_PORT="$BRIDGE_PORT"
        export BONGO_KEY_BRIDGE="$KEY_BRIDGE"
        [ "$CLICK_MODE" = "always" ] && export BONGO_FORCE_CLICKABLE=1 || export BONGO_FORCE_CLICKABLE=0
        [ "$MOUSE_CLICKS" = "real" ] && export BONGO_REAL_MOUSE=1 || export BONGO_REAL_MOUSE=0
        # Proton starts this .exe inside Bongo Cat's prefix, next to the game,
        # and stops it again when the game closes.
        export PROTON_REMOTE_DEBUG_CMD="\"$BRIDGE_DIR/keybridge.exe\""
    else
        notify "keybridge.exe is missing. Re-run install.sh."
    fi
fi

if [ "$KEY_BRIDGE" = "1" ]; then
    if command -v python3 >/dev/null 2>&1; then
        reader_args=(--port "$BRIDGE_PORT" --parent-pid "$$" --mouse-mode "$MOUSE_CLICKS")
        helper "$BRIDGE_DIR/keyreader.py" "${reader_args[@]}" >"$BRIDGE_DIR/keyreader.log" 2>&1 &
        READER_PID=$!
    else
        notify "python3 not found - the key bridge can't run."
    fi
fi

# --- 3. Hover mode: invisible mouse catcher (respects Gaming Mode) ------------
CATCHER_PID=""
if [ "$CLICK_MODE" = "hover" ] && [ -f "$BRIDGE_DIR/mousecatcher.py" ]; then
    helper "$BRIDGE_DIR/mousecatcher.py" --parent-pid "$$" >"$BRIDGE_DIR/mousecatcher.log" 2>&1 &
    CATCHER_PID=$!
fi

# --- 4. Run the game -----------------------------------------------------------
"$@"
rc=$?

[ -n "$READER_PID" ] && kill "$READER_PID" 2>/dev/null
[ -n "$CATCHER_PID" ] && kill "$CATCHER_PID" 2>/dev/null
exit $rc
