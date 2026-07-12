# Hearthstone Coach Overlay

A native Windows, always-on-top, Discord-style overlay for the existing `hearthstone-live-coach` workflow.

The tracker and AI coach still run in WSL. This Electron window runs on Windows and polls two small JSON files in a shared folder:

- `live.json` — mirrored by `hearthstone-tracker/hst live` from the game's Power.log state.
- `advice.json` — published by `hearthstone-tracker/coach_publish.py` after the AI coach writes its turn plan.

## Prerequisites

1. Install Node.js on Windows.
2. Run Hearthstone in **Windowed (Fullscreen)** / borderless mode. Native overlays cannot reliably float over exclusive fullscreen.
3. Use the default shared folder, or choose your own. The default is
   auto-detected: `hs-overlay` under your Windows user profile
   (`C:\Users\<you>\hs-overlay`, WSL: `/mnt/c/Users/<you>/hs-overlay`).
   Override with `overlayDir` in `config.json` (Windows side) and
   `HS_OVERLAY_DIR` / `HS_WINDOWS_USER` (WSL side).

## Run (browser mode — easiest, nothing to install on Windows)

Serve the overlay from WSL and open it in any Windows browser (WSL2 forwards
localhost automatically):

```bash
python3 serve.py
```

Then on Windows open `http://localhost:8420` — for a clean chromeless window:

```powershell
start msedge --app=http://localhost:8420/
```

Pin it over the game with PowerToys "Always on Top" (`Win+Ctrl+T`), or put it
on a second monitor. This mode has no click-through or global hotkeys — it is
a normal window — but needs no Node.js, no npm, and no Electron.

## Run (Electron mode — native always-on-top with click-through)

From Windows PowerShell, in this directory:

```powershell
copy config.example.json config.json
npm install
npm start
```

If you want a different shared folder, edit `config.json`:

```json
{
  "overlayDir": "C:/Users/<you>/hs-overlay"
}
```

From WSL, start the tracker feed as usual. `hst live` now mirrors `live.json` to the overlay folder automatically:

```bash
cd <repo>/hearthstone-tracker
./coach_feed.sh
```

To publish a sample advice card:

```bash
./coach_publish.py --kind lethal --turn 8 \
  --headline "Go face now" \
  --why "Visible damage reaches their effective HP." \
  --lethal-math "5+4+3 = 12 ≥ 12" \
  --step "Attack face with the 5/5" \
  --step "Weapon swing face" \
  --step "Cast burn face"
```

## Hotkeys

Defaults are configured in `config.json`:

- `Ctrl+Shift+F` — unlock move/resize mode for ALL panels; it re-locks to
  click-through automatically ~4s after you stop dragging (press again to
  re-lock instantly). While unlocked, drag a panel by its body and resize
  from any edge or corner.
- `Ctrl+Shift+9` — show/hide all panels
- `Ctrl+Shift+1/2/3/4` — toggle the advice / deck / opponent / lessons panel
- `Ctrl+Shift+-` / `Ctrl+Shift+=` — opacity down/up
- `Ctrl+Shift+0` — reset all panel positions

The window starts click-through so it will not eat Hearthstone clicks. Toggle click-through off to move or resize it, then toggle it back on.

## What the overlay shows — four standalone panels

Each panel is its own always-on-top window with saved position/size,
draggable and resizable on all four edges in move mode:

1. **Advice** — headline, why sentence, numbered moves, warning, **LETHAL**
   arithmetic, mulligan keep/toss rows, and the Discover PICK slot
   (`coach_publish.py --discover "Pick X — reason"` merges into the current
   card without replacing the turn plan).
2. **Deck** (HDT replacement) — your full decklist with card-art tiles, cost
   gems, ×N counts, draw odds; drawn cards grey out and shuffled/generated
   extras get their own group. Rows flash when a count changes.
3. **Opponent** — their class, HP, hand/deck counts, secrets, and every card
   they've played or revealed, newest first, with art tiles.
4. **Lessons** — coaching lessons accumulated across games (`--lesson` lines
   persist to `lessons.json`, deduped, newest first).
