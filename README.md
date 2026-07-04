# Hearthstone AI CLI Skills

> Portable AI-agent skills for building Hearthstone deck codes and choosing the cheapest competitive deck to craft from your collection.

[![CI](https://github.com/barnabys-drew/hearthstone-ai-cli-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/barnabys-drew/hearthstone-ai-cli-skills/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-standard%20library-only-brightgreen)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

This repository contains two `SKILL.md`-style agent skills that work together:

- **`hearthstone-deck-builder`** — build or verify a Hearthstone deck and produce a clipboard-ready import deck code.
- **`hearthstone-deck-recommender`** — compare your collection against current Standard meta decks and rank which decks are cheapest/easiest to complete.

The scripts are dependency-free Python and are designed to be used by Codex, Claude Code, Cursor, Gemini, or any AI CLI that can read a `SKILL.md` file.

> **Status:** Useful working prototype. Public-card/deck data changes often, so agents should still browse current top-deck sources before recommending a deck.

---

## Demo: “What should I craft?”

Given:

- a collection export (`collection.json`), and
- a list of current meta deckstrings (`meta_decks.json`),

run:

```bash
python3 hearthstone-deck-recommender/scripts/recommend_and_import.py \
  --collection collection.json \
  --decks meta_decks.json \
  --budget 4000
```

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
python3 hearthstone-deck-builder/scripts/build_deck_code.py \
  --input deck.json \
  --copy
```

### `hearthstone-deck-recommender`

Rank competitive decklists by how much dust you need to craft them from your collection.

What it does:

- Normalizes collection exports from HSReplay, Hearthstone Deck Tracker, Firestone, simple JSON maps, or CSV.
- Decodes each candidate deckstring and computes missing cards.
- Uses standard non-golden craft costs: Common 40, Rare 100, Epic 400, Legendary 1600.
- Treats Core-set cards as free/unlockable rather than craftable.
- Supports local collection files, collection JSON URLs, budgets, JSON output, and one-shot import output.

Examples:

```bash
# Ranking only
python3 hearthstone-deck-recommender/scripts/rank_decks.py \
  --collection collection.json \
  --decks meta_decks.json

# One-shot: rank + pick best + print Hearthstone import block
python3 hearthstone-deck-recommender/scripts/recommend_and_import.py \
  --collection-url "https://...account_lo=..." \
  --decks meta_decks.json \
  --budget 4000
```

---

## Quick start for humans

```bash
git clone https://github.com/barnabys-drew/hearthstone-ai-cli-skills.git
cd hearthstone-ai-cli-skills
python3 -m unittest discover -s tests
```

Try the deterministic sample fixtures:

```bash
python3 hearthstone-deck-recommender/scripts/recommend_and_import.py \
  --collection examples/collection.sample.json \
  --decks examples/meta_decks.sample.json \
  --cards-json examples/cards.sample.json \
  --no-fetch
```

The examples use tiny synthetic card IDs so tests are stable; they are not real playable meta decks.

---

## Installing into AI CLIs

These skills use ordinary `SKILL.md` folders. The cleanest setup is to keep this repository as the canonical source and symlink each skill folder into your AI CLI’s skill directory.

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
hearthstone-deck-builder/
  SKILL.md
  scripts/build_deck_code.py
  references/

hearthstone-deck-recommender/
  SKILL.md
  scripts/rank_decks.py
  scripts/recommend_and_import.py
  references/

examples/     # stable synthetic fixtures
tests/        # standard-library unittest suite
docs/         # collection/meta guides and public-release checklist
```

---

## Limitations

- The project does **not** log into Battle.net, scrape the local Hearthstone client, or bypass account protections.
- Meta deck recommendations are only as current as the deckstrings you provide. Agents should browse current top-deck sites before generating `meta_decks.json`.
- Card-name ambiguity is real in Hearthstone. For exact import correctness, DBF IDs are safer than names.
- Sideboard deckstring support follows the public deckstrings convention but should be tested against real edge-case decklists before heavy public promotion.

---

## Development

```bash
python3 -m unittest discover -s tests
python3 hearthstone-deck-builder/scripts/build_deck_code.py --selftest
```

No third-party Python packages are required.

---

## Attribution

- Card metadata is expected to come from the community HearthstoneJSON project or compatible local card JSON.
- Hearthstone is a trademark of Blizzard Entertainment. This project is an unofficial fan/tooling project and is not affiliated with or endorsed by Blizzard Entertainment.

---

## License

MIT — see [`LICENSE`](LICENSE).
