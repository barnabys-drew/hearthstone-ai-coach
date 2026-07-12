# Hearthstone AI Coach

> A model-agnostic AI coaching stack for Hearthstone: real-time turn-by-turn
> coaching over the game's own logs, a native five-panel overlay (advice, deck
> tracker, opponent tracker, lessons, deck stats), a trigger-matched lessons
> engine that learns from your misplays — plus the deck recommender/builder
> CLI tools it grew from. Doubles as a hands-on lab for progressive,
> cost-conscious RAG ([docs/progressive-rag.md](docs/progressive-rag.md)).
>
> *Formerly `hearthstone-deck-recommender` — old GitHub URLs redirect.*

## Why this exists

I came back to Hearthstone after a long break and the game handed me a pile of free
cards and dust. I had no idea what was worth building. Every meta site could tell me
what the best decks were, but none could tell me **which of those decks *my*
collection was already closest to finishing**. This tool answers exactly that: it
compares your collection against current top Standard decks, ranks them by the dust
you'd actually have to spend, and hands you a ready-to-import deck code.

[![CI](https://github.com/barnabys-drew/hearthstone-ai-coach/actions/workflows/ci.yml/badge.svg)](https://github.com/barnabys-drew/hearthstone-ai-coach/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
![Dependencies: standard library only](https://img.shields.io/badge/dependencies-standard%20library%20only-brightgreen)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## Two ways to use it

Both are first-class; pick per situation.

1. **Plain CLI (free, deterministic).** `hsdecks.py` is a single entry point over
   dependency-free Python scripts. Once you have a `collection.json`, one command
   fetches the current meta, ranks it against your cards, and prints an import code.
   No AI, no tokens, no third-party packages.
2. **AI-agent skills (guided).** The same scripts ship as two `SKILL.md` folders for
   Codex, Claude Code, Cursor, Gemini, or any AI CLI that reads skills. The agent
   earns its keep where a script can't: walking you through exporting your collection
   the first time, giving a conversational overview of the tradeoffs, and recovering
   when the deck-site scrape breaks by browsing current tier lists itself.

The skills:

- **`hearthstone-deck-builder`** — build or verify a Hearthstone deck and produce a clipboard-ready import deck code.
- **`hearthstone-deck-recommender`** — compare your collection against current Standard meta decks and rank which decks are cheapest/easiest to complete.
- **`hearthstone-live-coach`** — coach you turn by turn during a real game: the agent tails the game's own logs via the tracker's `hst live` feed and answers "what's my play?" with the actual hand, boards, and lethal math in front of it.
- **`hearthstone-overlay`** — start the native in-game overlay: four always-on-top panels (turn advice, HDT-style deck tracker, opponent tracker, accumulated coaching lessons) that render the live coach's advice next to the game.

Also in this repo:

- **[`hearthstone-tracker`](hearthstone-tracker/)** — a personal stats tracker
  that parses the game's own `Power.log` into SQLite and answers "which decks,
  matchups, mulligan keeps, and Battlegrounds heroes do I actually win with?"
  Plain CLI, no AI involved; unlike the skills it has one dependency
  ([hslog](https://github.com/HearthSim/python-hslog)), so it keeps its own
  `requirements.txt`. See its [README](hearthstone-tracker/README.md).

- **[`hearthstone-live-coach`](hearthstone-live-coach/SKILL.md)** — real-time
  in-game strategic advice. Tails the game's `Power.log` and emits a complete
  snapshot every turn: hand with card text, board with buffs, HP/armor/mana,
  remaining deck outs, opponent play history, and lethal math. An AI agent reads
  these snapshots and advises turn-by-turn, drawing from the actual game state
  (not model memory). Includes a rigorous checklist and traps from real-game
  validation. See its [architecture & checklist](hearthstone-live-coach/README.md).

- **[`hearthstone-overlay`](hearthstone-overlay/README.md)** — a native
  always-on-top overlay for the live coach: four standalone panels (turn
  advice, an HDT-style deck tracker with card-art tiles and draw odds, an
  opponent played-cards tracker, and coaching lessons accumulated across
  games). Runs as a Windows Electron app with click-through + hotkeys, or as
  a zero-install web page served from WSL (`serve.py`). The tracker's
  `hst live` mirrors game state to it automatically and the coach publishes
  plans via `coach_publish.py`.

- **[`hearthstone-post-game-coach`](hearthstone-post-game-coach/SKILL.md)** — analyze
  completed games to identify the deciding turn, suggest deck tweaks, and teach one
  mechanics lesson for next time. Works with tracker game history and Power.log.

- **[`hearthstone-deck-cleaner`](hearthstone-deck-cleaner/SKILL.md)** — identify
  which cards are underperforming in a deck and should be cut. Ranks cards by their
  win-rate delta vs. the deck's baseline, flags dead draws, and pairs with the
  deck recommender's substitute-suggestion logic to propose replacements.

> **Model-agnostic:** nothing in this repo calls an LLM API or depends on a
> specific model. The coach is whatever AI CLI you already use — Claude Code,
> Codex, Cursor, Gemini CLI, or a plain chat window — following the same
> SKILL.md playbook over the same feed. See
> [docs/coach-with-any-model.md](docs/coach-with-any-model.md) for per-agent
> setup.

> **Status:** Useful working prototype. Public-card/deck data changes often, so agents should still browse current top-deck sources before recommending a deck.

---

## Demo: “What should I craft?”

Given a collection export (`collection.json`), run:

```bash
python3 hsdecks.py recommend --collection collection.json
```

With no `--decks`, it fetches a batch of current Standard decks live from a public
deck site, pulls current card data from HearthstoneJSON, and ranks everything in one
shot. Pass `--decks meta_decks.json` to use your own candidate list instead.

Output looks like:

```text
 #     Dust   Own%  Leg Epic  Deck
------------------------------------------------------------------------
 1     1200    86%    0    2  Aggro Hunter [Hunter]  (54.2% WR)
 2     2800    74%    1    3  Rainbow Death Knight [Death Knight]

Easiest to build: Aggro Hunter (1200 dust, 86% owned).

========================================================================
COPY THIS INTO HEARTHSTONE
========================================================================
### Aggro Hunter
# Class: Hunter
# Format: Standard
# Dust needed from your collection: 1200
# Collection completion: 86.0%
#
AAECAR8...
#
```

Copy the deck code/import block, open Hearthstone, create a new deck, and accept the clipboard prompt.

The visual recommendation view separates:

- 🏆 **Best overall** from the fetched meta sample
- ✅ **Best deck you can afford** with detected/provided dust
- 🎯 **Best close/easy craft** (default import pick)
- 💸 **Cheapest deck**
- Color-coded dust tiers so the shortlist is easier to scan

---

## Skills

### `hearthstone-deck-builder`

Build a Hearthstone deck and produce a verified **deckstring** — the import code the game reads from your clipboard.

What it does:

- Encodes/decodes Hearthstone deckstrings (varint + base64).
- Resolves card names to DBF IDs via HearthstoneJSON or a local card file.
- Supports Standard/Wild/Twist/Classic format IDs, heroes, and sideboards such as E.T.C.-style extra cards.
- Validates basic constructed constraints and round-trips generated codes before printing them.

Example:

```bash
python3 hsdecks.py build --input deck.json --copy
```

### `hearthstone-deck-recommender`

Rank competitive decklists by how much dust you need to craft them from your collection.

What it does:

- Normalizes collection exports from HSReplay, Hearthstone Deck Tracker, Firestone, simple JSON maps, or CSV.
- Decodes each candidate deckstring and computes missing cards.
- Uses standard non-golden craft costs: Common 40, Rare 100, Epic 400, Legendary 1600.
- Treats Core-set cards as free/unlockable rather than craftable.
- Supports local collection files, collection JSON URLs, budgets, JSON output, and one-shot import output.
- Can auto-fetch a batch of current Standard deck codes so you don't have to assemble candidates by hand.

Examples:

```bash
# One-shot with live data: fetch current Standard decks + card data, rank, import block
python3 hsdecks.py recommend --collection collection.json

# Optional: save current Standard deck candidates for reuse/inspection
python3 hsdecks.py fetch-decks --out meta_decks.json --limit 40

# Ranking only, against a saved candidate list
python3 hsdecks.py rank --collection collection.json --decks meta_decks.json

# One-shot with a live collection URL and saved decks
python3 hsdecks.py recommend \
  --collection-url "https://...account_lo=..." \
  --decks meta_decks.json \
  --view visual \
  --pick-policy close
```

`hsdecks.py` is a thin dispatcher: each subcommand accepts exactly the flags of the
underlying script in `hearthstone-deck-builder/scripts/` and
`hearthstone-deck-recommender/scripts/`, which remain directly runnable (that is what
the AI skills call).

---

## Quick start (no AI required)

```bash
git clone https://github.com/barnabys-drew/hearthstone-ai-coach.git
cd hearthstone-ai-coach
python3 -m unittest discover -s tests
```

Export your collection (see [`docs/collection-guide.md`](docs/collection-guide.md)),
save it as `collection.json`, then:

```bash
python3 hsdecks.py recommend --collection collection.json --view visual
```

Or try the deterministic sample fixtures first:

```bash
python3 hsdecks.py recommend \
  --collection examples/collection.sample.json \
  --decks examples/meta_decks.sample.json \
  --cards-json examples/cards.sample.json \
  --no-fetch
```

The examples use tiny synthetic card IDs so tests are stable; they are not real playable meta decks.

---

## Installing into AI CLIs (optional)

Everything above works without an AI. Install the skills when you want the guided
experience: first-time collection export walkthroughs, a conversational overview of
which deck to pick and why, and automatic recovery (the agent browses current tier
lists) when the deck-site scrape comes back empty.

The skills use ordinary `SKILL.md` folders. The cleanest setup is to keep this repository as the canonical source and symlink each skill folder into your AI CLI’s skill directory.

```bash
# Codex-style skills
mkdir -p ~/.codex/skills
ln -s "$PWD/hearthstone-deck-builder" ~/.codex/skills/hearthstone-deck-builder
ln -s "$PWD/hearthstone-deck-recommender" ~/.codex/skills/hearthstone-deck-recommender

# Claude Code-style skills
mkdir -p ~/.claude/skills
ln -s "$PWD/hearthstone-deck-builder" ~/.claude/skills/hearthstone-deck-builder
ln -s "$PWD/hearthstone-deck-recommender" ~/.claude/skills/hearthstone-deck-recommender

# Cursor personal skills
mkdir -p ~/.cursor/skills
ln -s "$PWD/hearthstone-deck-builder" ~/.cursor/skills/hearthstone-deck-builder
ln -s "$PWD/hearthstone-deck-recommender" ~/.cursor/skills/hearthstone-deck-recommender
```

For Gemini or tools without native skill folders, add an instruction file such as `GEMINI.md` or `AGENTS.md`:

```md
When asked to build Hearthstone decks, read `hearthstone-deck-builder/SKILL.md`.
When asked which deck to craft from a collection, read `hearthstone-deck-recommender/SKILL.md`.
```

---

## Data sources and privacy

There is currently no official public Blizzard API for reading a player’s full Hearthstone collection. The recommender supports practical export paths instead:

- HSReplay `collection/mine` JSON after syncing through a deck tracker.
- Hearthstone Deck Tracker or Firestone collection exports.
- Manual JSON/CSV maps of `dbfId -> owned count`.

Read the detailed guides: [`docs/collection-guide.md`](docs/collection-guide.md) and [`docs/meta-decks-guide.md`](docs/meta-decks-guide.md).

**Important:** Collection files and browser cookies can reveal account-specific information. Do not commit real `collection.json` files, HSReplay `account_lo` URLs, or Cookie headers. `.gitignore` excludes the common local filenames.

---

## Repository layout

```text
hsdecks.py    # unified CLI entry point (recommend | rank | fetch-decks | build)

hearthstone-deck-builder/
  SKILL.md
  scripts/build_deck_code.py
  references/

hearthstone-deck-recommender/
  SKILL.md
  scripts/fetch_meta_decks.py
  scripts/rank_decks.py
  scripts/recommend_and_import.py
  references/

hearthstone-live-coach/
  SKILL.md     # live turn-by-turn coaching playbook over the tracker's hst live feed

hearthstone-overlay/
  main.js      # Electron: 4 always-on-top panels (advice/deck/opponent/lessons)
  renderer/    # shared panel UI (also served as a web page)
  serve.py     # zero-install browser mode: serves panels + JSON from WSL

hearthstone-tracker/
  hst          # CLI launcher (backfill | watch | live | stats)
  hstracker/   # Power.log -> SQLite capture, live snapshots, stats, overlay bridge
  coach_publish.py  # publish coach advice/discover picks/lessons to the overlay
  requirements.txt

examples/     # stable synthetic fixtures
tests/        # standard-library unittest suite (tracker tests need hslog)
docs/         # collection/meta guides, model-agnostic coach setup,
              # progressive-rag.md: the lessons engine as a RAG learning lab
```

---

## Limitations

- The deck tools do **not** log into Battle.net or bypass account protections.
  The tracker reads the game's own log files (`Power.log`/`Decks.log`), the
  officially sanctioned mechanism that deck trackers like HDT use — nothing
  reads game memory or network traffic.
- The default live fetch scrapes one public deck site (hearthstone-decks.net). Site layouts change; if the fetch comes back empty, browse current top-deck sites and pass `--decks meta_decks.json` instead. Recommendations are only as current as the decks that come in.
- There is no HSReplay meta-statistics integration (no official public API); win rates appear only when your deck entries include them.
- Card-name ambiguity is real in Hearthstone. For exact import correctness, DBF IDs are safer than names.
- Sideboard deckstring support follows the public deckstrings convention but should be tested against real edge-case decklists before heavy public promotion.

---

## Development

```bash
python3 -m unittest discover -s tests
python3 hearthstone-deck-builder/scripts/build_deck_code.py --selftest
```

The deck tools require no third-party Python packages. The tracker's tests
skip automatically unless its one dependency is installed
(`pip install -r hearthstone-tracker/requirements.txt`); CI installs it so
they always run there.

---

## Attribution

- Card metadata is expected to come from the community HearthstoneJSON project or compatible local card JSON.
- Hearthstone is a trademark of Blizzard Entertainment. This project is an unofficial fan/tooling project and is not affiliated with or endorsed by Blizzard Entertainment.

---

## License

MIT — see [`LICENSE`](LICENSE).
