# hearthstone-tracker

Personal Hearthstone game tracker: parses the game's `Power.log` (via
[hslog](https://github.com/HearthSim/python-hslog)) into a local SQLite
database and answers "which decks/classes/matchups do I actually win with?"

Runs from WSL against a Windows Hearthstone install (log folders are found
automatically under `/mnt/*/Battle.net/Hearthstone/Logs`).

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Hearthstone must have Power logging enabled in
`%LOCALAPPDATA%\Blizzard\Hearthstone\log.config` (a `[Power]` section with
`FilePrinting=True` and `Verbose=True`). Installing any deck tracker sets
this up; it can also be added by hand.

## Usage

```bash
./hst backfill          # import every game found in existing log folders
./hst watch             # tail the live log; records each game as it ends
./hst live              # stream the current game's state, turn by turn
./hst stats             # all stat views
./hst stats deck --game-type ranked --format standard
./hst stats matchup --game-type ranked --format standard --min-games 3
./hst stats cards --deck "Burn Warrior" --min-games 5
./hst stats mulligan --game-type ranked
./hst stats recent --limit 10
./hst rag-report        # lesson-retrieval telemetry: firing rates, dead knowledge, misses
./hst rag-replay Hearthstone_2026_07_12_04_50_10   # replay a past session against the current lesson store
```

Views: `overall`, `deck`, `class`, `matchup`, `first` (going first vs coin),
`cards` (win rate when a card is played/drawn), `mulligan` (keep rate and win
rate when kept), `bg` (Battlegrounds placement/hero stats), `bg-heroes`
(hero offer/pick rates), `recent`. The card views accept `--deck <substring>`
to scope to one deck.

Retrieval telemetry (`docs/progressive-rag.md` Phase 1): `hst live` appends
one event per your-turn to `~/.local/share/hearthstone-tracker/retrieval_log.jsonl`
recording which triggered lessons fired (or that none did), plus a game
outcome event. `hst rag-report` turns that log into per-lesson firing rates,
dead knowledge, retrieval misses, and a precision proxy; `hst rag-replay
<session-dir>` re-runs the current lesson store against a past session's
Power.logs (deterministic — diff two `--json` runs to regression-test a store
change). Replay never writes the live log.

Example:

```
$ ./hst stats deck
## Win rate by deck
deck          class    games  wins  winrate
------------  -------  -----  ----  -------
Burn Warrior  WARRIOR  42     25    59.5

$ ./hst stats bg
## Battlegrounds
mode  games  avg_place  top4_pct  first_pct  avg_tier
----  -----  ---------  --------  ---------  --------
solo  30     4.2        56.7      10.0       4.8
```

The live watcher prints each game as it finishes:

```
[2026-07-04 16:24:22] GT_RANKED Burn Warrior vs DEATHKNIGHT (opponent#1234): WON in 15 turns
[2026-07-04 17:34:48] GT_BATTLEGROUNDS Rokara: place 3/8 (tier 5)
```
Game types accept shortcuts (`ranked`, `casual`, `arena`, `bg`) or raw `GT_*`
values; formats accept `standard`/`wild`/`twist` or `FT_*`.

The database lives at `~/.local/share/hearthstone-tracker/games.db`
(override with `--db`). Re-running `backfill` or `watch` never duplicates
games — inserts are keyed on (start time, both player names).

## What gets recorded

One row per completed game: start/end time, duration, game type and format,
both players' names, heroes and classes, who went first, result, turn count,
and — for constructed modes — the deck name and deckstring you queued with.
Games are only recorded once the log shows `STATE=COMPLETE`, so crashes and
partial logs don't produce bogus rows.

Deck names come from `Decks.log` (the `[Decks]` section in `log.config`;
requires a game restart after enabling). Each game is matched to the most
recent "Finding Game With Deck" event before it started; Battlegrounds and
Mercenaries games never get a deck attached.

Constructed games additionally get per-card events in the `game_cards`
table: draws (deck→hand only, not created copies), plays (includes hero
power), first turn played, and mulligan offered/kept — for both players'
revealed cards. This powers the `cards` and `mulligan` views. BG/Mercs games
are excluded (their "mulligan" is a hero pick).

## Live game state (`hst live`)

`./hst live` tails the game in progress and, each turn, prints a compact
snapshot plus writes the full state as JSON to
`~/.local/share/hearthstone-tracker/live.json` (atomic writes; override with
`--json-file`). `--once` prints a single snapshot and exits. Snapshots
include: your hand with costs **plus each card's type and rules text** (so an
agent reasons from what the card actually does, not from what it remembers
about the card), both boards with buff/`damaged` flags,
HP/armor/mana, weapons, opponent hand count and secrets, **which cards are
probably still in your deck** (your Decks.log decklist minus what you've
drawn — this is what turns "hope for burn" into "3 of my 8 remaining cards
win the race"), and **everything the opponent has played so far**. A
`== MULLIGAN` marker fires with your dealt cards before you lock in keeps.

Only client-visible information exists in the log — the opponent's hand
appears as a count of hidden cards, so this is a legal-information tool, not
a cheat. Constructed-focused for now (Battlegrounds shop view is a possible
later addition).

The intended workflow with an AI CLI: run `hst live` in the background, watch
its stdout for lines starting with `== ` (mulligan, each turn, game over),
and on each of your turns have the agent read `live.json` and suggest a play.
`hst live` also mirrors the latest snapshot to a Windows overlay folder
(default: `/mnt/c/Users/<you>/hs-overlay`, override with `--overlay-dir` or
`HS_OVERLAY_DIR`) so the native `../hearthstone-overlay` app can render a
Discord-style tracker rail. The AI coach can publish its numbered plan to the
same folder with `./coach_publish.py`, which writes `advice.json` atomically.
The [`hearthstone-live-coach`](../hearthstone-live-coach/SKILL.md) skill packages
this workflow as a model-agnostic playbook (setup, per-turn checklist, output
format, known traps) so coaching quality doesn't depend on which model runs it.
Markers land within a few seconds of the real event. Battle-tested the day it
was written: its first coached game came down to exact lethal arithmetic —
the tool called "dead by 2 if everything goes face", and everything went
face.

Implementation notes: each poll re-parses only the current game's lines with
a fresh hslog parser — a single parser fed a multi-game Power.log can raise
`InconsistentPlayerIdError` when player ids shuffle between games, and
per-game parsing keeps polls fast (~0.5s) regardless of session length.

### Prefer post-game-only coaching? Skip `hst live` entirely

If turn-by-turn advice is more interruption than help, `hst live` isn't
needed at all — [`hearthstone-post-game-coach`](../hearthstone-post-game-coach/SKILL.md)
only needs the tracker database and Power.log, both of which `./hst watch`
already provides. Run `hst watch` instead (records each completed game,
no per-turn snapshot spam, no `live.json`), and point a Monitor/tail at its
own stdout for the completion line it already prints:

```
[2026-07-05 10:24:22] GT_RANKED Burn Warrior vs HUNTER (opponent#1234): WON in 11 turns
```

On that line, invoke `hearthstone-post-game-coach` — it pulls the game via
`./hst stats recent --limit 1` and analyzes from there. This is strictly
lighter weight than `hst live` (no live JSON writes, no per-turn parsing)
and is the right choice if you only want the deciding-turn/deck-feedback/
mechanics-lesson report after each game, not in-the-moment lethal math.

## Battlegrounds

BG games record final placement (`bg_place`), the hero you picked
(`friendly_hero`), the heroes you were offered (as hero-pick rows in
`game_cards`), and the highest tavern tier you reached (`bg_tech`). The
`result` column still reflects Hearthstone's own playstate — only 1st place
counts as WON — so use `stats bg` (average place, top-4 %, 1st %) for real BG
analysis. Duos games are expected to work via the same tags but are
unverified until one is logged.

## Notes

- Hearthstone creates a new `Hearthstone_YYYY_MM_DD_HH_MM_SS` log folder per
  launch and rotates `Power.log` → `Power_old.log`; both are handled.
- Hero classes and names are resolved from a cached HearthstoneJSON dump
  (`~/.cache/hearthstone-tracker/cards.json`), with a built-in `HERO_NN`
  fallback when offline.

## Ideas / next steps

- Opponent archetype detection from their revealed cards.
- BG rating (MMR) is not in Power.log; tracking it would need another source.
