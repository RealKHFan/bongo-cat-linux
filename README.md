# Bongo Cat on Linux

An unofficial helper that makes the Steam game **Bongo Cat** work properly on Linux through Proton. With it, the cat is see-through, reacts to your typing in other apps, and can be clicked.

This project is not affiliated with Bongo Cat or its developers. It doesn't modify the game; it only adds small helpers that run next to it.

| Problem under Proton | What fixes it |
|---|---|
| Black box instead of a see-through background | Bongo Cat's own **Transparency Fix** (F3), which works on Proton Experimental |
| The cat ignores typing in other apps | the **key bridge** (`keyreader.py` + `keybridge.exe`) |
| The cat can't be clicked | the **click fix** (inside `keybridge.exe`) |

---

## Will it work on my PC?

**Tested on:** Bazzite (KDE Plasma 6, Wayland), native Steam, Proton Experimental.

**Should work on:** any Linux desktop that runs Steam with Proton Experimental. That includes:

- Arch, Fedora, Ubuntu, Mint, openSUSE, SteamOS Desktop Mode and others
- KDE or GNOME, on Wayland or X11

**You need:**

- Steam with **Proton Experimental**.
- `python3`, which almost every distro already has.
- Your password once, so the installer can add you to the `input` group.

**Untested:** Flatpak Steam. The installer offers the permission it needs.

**Won't work:** Steam Deck Game Mode, or any other gamescope session. Desktop overlays can't be shown there, so use Desktop Mode.

---

## Install

### Step 1: Download

On this page, click **Code → Download ZIP** and extract it. Or clone it:

```
git clone <this repository's URL>
```

### Step 2: Run the installer

Open a terminal inside the downloaded folder. In most file managers you can right-click an empty spot and choose **Open Terminal Here**. Then run:

```
bash install.sh
```

Answer **Y** to its questions. The installer does four things:

- Copies everything to `~/.local/share/bongo-bridge/`.
- Adds you to the `input` group so the key bridge can read your keyboard. It asks for your password. On a Steam Deck, set a password first with `passwd`.
- If you use Flatpak Steam, lets Steam see the bridge folder.
- Prints your **launch options line** and saves it to `~/.local/share/bongo-bridge/LAUNCH-OPTIONS.txt`.

### Step 3: Log out and back in

Only needed if the installer said so. The `input` group only takes effect in a new session.

### Step 4: Check that your keyboard can be read

```
python3 ~/.local/share/bongo-bridge/keyreader.py --test
```

Type a few letters. You should see `tap  left paw` and `tap  right paw`, one line per key. Press **Ctrl+C** to stop.

If it says *permission denied*, go back to Step 3.

### Step 5: Use Proton Experimental

In Steam, right-click **Bongo Cat → Properties → Compatibility**. Tick **Force the use of a specific Steam Play compatibility tool** and choose **Proton Experimental**.

### Step 6: Set the launch options

In **Properties → General → Launch Options**, paste the line from `LAUNCH-OPTIONS.txt`. It looks like this:

```
/home/YOURNAME/.local/share/bongo-bridge/bongo-launch.sh %command%
```

Copy it exactly. It must start with a `/`, or the game closes immediately.

### Step 7: First launch, then press F3

Launch Bongo Cat. On the very first launch, Steam may say the game is already running. If so, press **Stop** and launch again.

As soon as you see the cat, press **F3** once. That turns on Bongo Cat's Transparency Fix: the background becomes see-through, and the click fix can make the cat clickable.

If nothing happens, Alt+Tab to Bongo Cat (or click its taskbar entry) and press F3 again.

### Step 8: Go through Bongo Cat's setup

Click through the setup screens. When it asks **whether your window is transparent, answer No**.

- Answering **Yes** switches Bongo Cat to a mode Proton can't display, and the cat vanishes on the next start.
- Pressing **F3 a second time** also makes the cat disappear.

### Step 9: Check, then back up

Check that everything works:

- Type in another app. The cat should tap along.
- Click the cat. It should respond, and clicks beside the cat should still reach whatever is behind it.

Once it works, back up this state:

1. Close Bongo Cat.
2. In Steam, go to Bongo Cat → **Properties → Installed Files → Browse**.
3. Go up two folders to `steamapps`, then open `compatdata`.
4. Copy the folder `3419430` and name the copy `3419430-good`.

If the cat ever disappears, delete `3419430` and rename `3419430-good` back to `3419430`.

---

## Updating

1. Close Bongo Cat.
2. Download the new version and run `bash install.sh` again. Your settings are kept.
3. Leave your launch options as they are.

---

## Settings

Open `~/.local/share/bongo-bridge/config.env` in a text editor, change a value, save, and relaunch Bongo Cat.

| Setting | Values | What it does |
|---|---|---|
| `KEY_BRIDGE` | `1` / `0` | Cat reacts while you type in other apps |
| `MOUSE_CLICKS` | `real` (default), `paw`, `off` | `real` replays your clicks as actual mouse clicks anywhere on the desktop. `paw` taps a paw instead. |
| `CLICK_MODE` | `always` (default), `hover`, `off` | `always` makes the cat always clickable. `hover` only makes it clickable while the mouse is over it and respects Gaming Mode, but doesn't work during Bongo Cat's first-time setup. |
| `TRANSPARENCY_MODE` | `off` | Only for GE-Proton experiments; leave it `off` |
| `BRIDGE_PORT` | `47811` | Only change it if a log says the port is in use |

---

## Troubleshooting

Logs are in `~/.local/share/bongo-bridge/`: `keyreader.log`, `keybridge.log` and, in hover mode, `mousecatcher.log`.

| Problem | Fix |
|---|---|
| Game closes right away | The launch options path is wrong. Paste it again from `LAUNCH-OPTIONS.txt`; it must start with `/`. |
| "Already running" on the first launch | Press **Stop** and launch again. |
| Black background | Alt+Tab to Bongo Cat and press **F3**. |
| Cat can't be clicked | The Transparency Fix must be on, so press **F3**. `keybridge.log` should then say *click fix: made window ... clickable*. |
| Cat vanished | You answered Yes in the setup, or pressed F3 twice. Close the game and restore `3419430-good` (Step 9), or delete `compatdata/3419430` and redo Steps 7–8. |
| Cat invisible for another reason | Press **F8** right after launching. Once the cat is focused, **F1** resets its position. |
| Mashing many keys only counts a few taps | Update to the current version: older ones sent every key as one of two letters, which Bongo Cat could only count twice. |
| Mouse clicks elsewhere don't count | Set `MOUSE_CLICKS=real` in `config.env`. |
| Cat doesn't react to typing elsewhere | If `keyreader.log` says *permission denied*, redo Steps 2–3. If `keybridge.log` doesn't exist, the launch options aren't set. |
| Something acts strange in a menu | Restart Bongo Cat. |
| "steamwebhelper is not responding" | That's Steam itself. Choose **Restart Steam**. |

---

## How it works

**Transparency.** Bongo Cat's Transparency Fix fills the background with one key colour. Wine turns that colour into a cut-out window shape.

Why not GE-Proton? GE-Proton 11-6 has its own transparency patch, but with it Bongo Cat's window became completely invisible.

**Typing.** On Linux, Wine only receives keys while one of its own windows is focused, so Bongo Cat can't hear you type in other apps.

- `keyreader.py` reads your keyboard from `/dev/input`. It only forwards which half of the keyboard you hit, never the key itself: every key that is down gets its own letter from that side's pool of 26, handed out in turn, so the letter says nothing about what you pressed.
- Giving each key its own letter is what makes mashing work. Bongo Cat counts a tap per key, so if everything mapped to one letter per side, 30 keys at once would only ever count as 2 taps.
- Proton starts `keybridge.exe` inside Bongo Cat's prefix, using Proton's `PROTON_REMOTE_DEBUG_CMD` hook, and stops it when the game closes.
- `keybridge.exe` replays each tap with `SendInput()` as a full press, short hold, release. The hold matters: a game that checks key state once a frame would miss a press and release that happen in the same instant.
- Mouse clicks are replayed as real clicks. While an injected click is in flight, Bongo Cat's window is briefly set to pass clicks through, so the click reaches its global mouse hook but can't press any of its buttons. Clicking the cat on purpose still works normally.
- While you type directly into Bongo Cat's own window, forwarding pauses so presses aren't counted twice.

**Clicking.** Bongo Cat marks its window as click-through and only lifts that while it sees the mouse over the cat. On Wayland it can't see the mouse over normal Linux apps, so the cat stays unclickable.

With the Transparency Fix on, the see-through area is already a real hole in the window. So `keybridge.exe` simply removes the click-through mark. Clicks on the cat reach Bongo Cat, and clicks on the see-through area pass through to whatever is behind it.

`CLICK_MODE=hover` instead runs `mousecatcher.py`. It places an invisible window on the cat's outline so Bongo Cat can notice the mouse itself, which keeps Gaming Mode working.

---

## Files in this repository

| File | What it is |
|---|---|
| `install.sh` | Installer and updater |
| `bongo-launch.sh` | Steam launch wrapper: starts the helpers, then the game |
| `config.env` | Default settings (copied on first install only) |
| `keyreader.py` | Linux side of the key bridge (pure Python, no extra packages) |
| `keybridge.c` | Source of the Windows side: key replay + click fix |
| `keybridge.exe` | `keybridge.c`, compiled |
| `mousecatcher.py` | Optional hover-mode helper (uses the system's libX11/libXext) |
| `build-helper.sh` | Rebuilds `keybridge.exe` from source |
| `uninstall.sh` | Removes the installed files |

### Building keybridge.exe yourself

You don't have to trust the included binary. Run this to rebuild it:

```
bash build-helper.sh
```

It uses a local MinGW compiler if you have one, otherwise a temporary Fedora container through podman or docker.

The manual equivalent is:

```
x86_64-w64-mingw32-gcc -O2 -s -mwindows -o keybridge.exe keybridge.c -lws2_32
```

The included binary's SHA-256 is `2c67979ce20824a415d2ff2cf5d20f5183c0842d6e557b9a51ba5ab83d75c4ef`. Your own build may differ byte-for-byte if you use a different compiler version.

---

## Privacy and safety

- Your actual keys never leave `keyreader.py`. It sends only which side of the keyboard you hit, as the next free letter from that side's pool, and only to your own machine. The letter carries no trace of the key you pressed.
- `keybridge.exe` only ever presses the letters A–Z and the left mouse button. No modifiers, no function keys, no shortcuts.
- The click fix only changes the click-through flag of windows that use Bongo Cat's Transparency Fix.
- Being in the `input` group lets any program running as you read keyboard input. Tools like Input Remapper need the same access. To leave the group again, run `sudo gpasswd -d $USER input` and log out.
- In `CLICK_MODE=always`, Bongo Cat's Gaming Mode can no longer block clicks on the cat. Use `hover` if you rely on Gaming Mode.

---

## Uninstall

Run:

```
~/.local/share/bongo-bridge/uninstall.sh
```

Then clear Bongo Cat's launch options in Steam.

---

## License

MIT. See [LICENSE](LICENSE).
