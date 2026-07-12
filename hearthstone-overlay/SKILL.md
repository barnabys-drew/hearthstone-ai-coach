---
name: hearthstone-overlay
description: Start (or restart) the Hearthstone coach overlay — four native always-on-top panels (turn advice, HDT-style deck tracker, opponent tracker, coaching lessons) fed by the hearthstone-tracker live feed. Use when the user asks to start/launch/restart the overlay, says panels are missing or stale, or wants the coach's advice shown in-game instead of only in chat. Pairs with hearthstone-live-coach, which publishes the advice this overlay displays.
---

# Hearthstone Coach Overlay — startup

Bring up the full overlay stack: the WSL feed that mirrors game state, and the
four Windows panels that render it. Everything here was learned the hard way —
follow the exact commands, the quoting workarounds matter.

## Architecture (10-second version)

- WSL: `coach_feed.sh` runs `hst live`, which mirrors `live.json` into the
  shared folder `/mnt/c/Users/$WINUSER/hs-overlay` (Windows:
  `C:\Users\%WINUSER%\hs-overlay`).
- WSL: `coach_publish.py` writes `advice.json` (turn plans, discover picks)
  and `lessons.json` (accumulating coaching lessons) into the same folder.
- Windows: an Electron app (copied to `C:\Users\%WINUSER%\hearthstone-overlay`)
  polls those files and renders four standalone always-on-top panels.

## Start procedure

1. **Start the feed** (kills zombies, prints `FEED_PID=` and `LOG=` on
   success — coordinate with hearthstone-live-coach, which watches that LOG):

   ```bash
   <repo>/hearthstone-tracker/coach_feed.sh
   ```

   Verify its startup lines include `Overlay mirror: /mnt/c/Users/$WINUSER/hs-overlay`.
   If that line is missing, the running code predates the overlay — the feed
   restart just fixed it.

2. **Sync the Windows app copy** whenever repo overlay files changed (npm is
   unreliable on WSL UNC paths, so the app runs from a Windows-local copy):

   ```bash
   cp <repo>/hearthstone-overlay/main.js <repo>/hearthstone-overlay/preload.js \
      <repo>/hearthstone-overlay/package.json <repo>/hearthstone-overlay/config.example.json \
      /mnt/c/Users/$WINUSER/hearthstone-overlay/
   cp <repo>/hearthstone-overlay/renderer/*.* /mnt/c/Users/$WINUSER/hearthstone-overlay/renderer/
   ```

   Do NOT overwrite `C:\Users\%WINUSER%\hearthstone-overlay\config.json` — it holds
   the user's saved panel positions. Only seed it from `config.example.json`
   if it does not exist.

3. **Restart the Electron app** (from WSL; `start` detaches, output goes to a
   log so hotkey-registration failures are visible):

   ```bash
   cd /mnt/c && cmd.exe /c "taskkill /im electron.exe /f" >/dev/null 2>&1
   sleep 1
   nohup cmd.exe /c "C:\Users\%WINUSER%\hearthstone-overlay\node_modules\electron\dist\electron.exe C:\Users\%WINUSER%\hearthstone-overlay > C:\Users\%WINUSER%\hearthstone-overlay\electron.log 2>&1" >/dev/null 2>&1 &
   sleep 4
   cat /mnt/c/Users/$WINUSER/hearthstone-overlay/electron.log  # empty = all hotkeys registered
   cmd.exe /c "tasklist" 2>/dev/null | grep -ci electron    # ~7 processes = 4 panels up
   ```

   Windows-side gotchas (all real):
   - `cmd.exe` from WSL mangles quoted paths — use the 8.3 short path
     `C:\PROGRA~1\nodejs\...` instead of quoting `Program Files`.
   - Electron's postinstall needs `node` on PATH:
     `cmd.exe /c "set PATH=C:\PROGRA~1\nodejs;%PATH%&& cd /d C:\Users\%WINUSER%\hearthstone-overlay && npm.cmd install"`,
     and if npm's allow-scripts blocks it, run
     `cd node_modules\electron && node install.js` the same way.
   - If Node.js is missing entirely:
     `cmd.exe /c "winget install --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements"`
     (a UAC prompt appears on the Windows screen).

4. **Publish a visible card** so the user can confirm the advice panel works:

   ```bash
   <repo>/hearthstone-tracker/coach_publish.py --kind idle \
     --headline "Overlay online" --why "Panels are live; start a game."
   ```

5. **Tell the user the controls** (they forget between sessions):
   - `Ctrl+Shift+F` — unlock move/resize for all panels (purple glow); drag a
     card body to move, any edge/corner to resize; auto-relocks ~4s after the
     last drag.
   - `Ctrl+Shift+1/2/3/4` — toggle advice / deck / opponent / lessons panel.
   - `Ctrl+Shift+9` — show/hide all. `Ctrl+Shift+0` — reset layout.
     `Ctrl+Shift+-` / `Ctrl+Shift+=` — opacity.

## Fallback: browser mode (no Node/Electron)

If the Electron path is broken, serve the same panels as a web page from WSL
and open `http://localhost:8420` on Windows (WSL2 forwards localhost). No
click-through, but zero install:

```bash
python3 <repo>/hearthstone-overlay/serve.py
```

## Known failure modes

- **Panels show stale/no data**: the feed died or predates a code change —
  rerun `coach_feed.sh` (it replaces the old process) and check for the
  `Overlay mirror:` line.
- **A hotkey silently does nothing**: another app owns it. `electron.log`
  prints `Hotkey <accel> is taken by another app` — pick a new accelerator in
  `config.json` (several Ctrl+Shift+letter combos are taken on this machine;
  digits registered reliably).
- **Card art missing**: tiles stream from `art.hearthstonejson.com`; offline
  machines get plain dark rows. Harmless.
