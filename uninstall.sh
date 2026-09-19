#!/usr/bin/env bash
# Removes the Bongo Cat bridge. Bongo Cat itself is left alone.
DEST="$HOME/.local/share/bongo-bridge"
read -rp "Remove $DEST? [Y/n] " a
[[ "$a" =~ ^[Nn] ]] && exit 0
rm -rf "$DEST"
echo "Removed."
echo
echo "Two things to do by hand:"
echo " 1. Clear Bongo Cat's Launch Options in Steam."
echo " 2. Optional - leave the 'input' group again:   sudo gpasswd -d $USER input   (then log out/in)"
