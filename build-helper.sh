#!/usr/bin/env bash
# Builds keybridge.exe from keybridge.c.
# Uses a local MinGW compiler if you have one, otherwise a throwaway Fedora
# container (podman or docker).
set -e
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
CC_ARGS=(-O2 -s -mwindows -o keybridge.exe keybridge.c -lws2_32)

if command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1; then
    echo "Building with your local MinGW compiler..."
    (cd "$DIR" && x86_64-w64-mingw32-gcc "${CC_ARGS[@]}")
else
    RUNNER=""
    command -v podman >/dev/null 2>&1 && RUNNER=podman
    [ -z "$RUNNER" ] && command -v docker >/dev/null 2>&1 && RUNNER=docker
    if [ -z "$RUNNER" ]; then
        echo "Need either x86_64-w64-mingw32-gcc (package: mingw64-gcc / gcc-mingw-w64-x86-64 / mingw-w64-gcc)"
        echo "or podman/docker. Install one of them and run this again."
        exit 1
    fi
    echo "Building in a Fedora container with $RUNNER (first run downloads a few hundred MB)..."
    "$RUNNER" run --rm -v "$DIR":/src:Z registry.fedoraproject.org/fedora:latest bash -c \
      "dnf -y -q install mingw64-gcc >/dev/null && cd /src && x86_64-w64-mingw32-gcc ${CC_ARGS[*]}"
fi
echo "Done: $DIR/keybridge.exe"
sha256sum "$DIR/keybridge.exe"
