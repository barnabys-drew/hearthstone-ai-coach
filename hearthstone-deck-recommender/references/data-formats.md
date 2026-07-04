# Input data formats

## Collection

The script normalizes several shapes into `{dbfId: owned_count}`. Owned count sums all
finishes (normal + golden + diamond + signature) because any copy fills a deck slot.

You can load collections from disk with `--collection collection.json` or directly from a
JSON URL with `--collection-url`. Private HSReplay URLs may require copying the JSON from
DevTools, or supplying a Cookie header from your browser session via
`--collection-cookie-file PATH` ('-' reads stdin) or the `HS_COLLECTION_COOKIE` /
`HS_COLLECTION_COOKIE_FILE` environment variables. The raw `--collection-cookie`
flag is deprecated: it leaks into shell history and process listings. Do
not commit Cookie headers or paste them into shared logs.

### HSReplay collection/mine JSON (preferred)

```json
{
  "collection": {
    "1234": [2, 0, 0, 0],
    "5678": [0, 1, 0, 0]
  },
  "dust": 3200
}
```

The top-level `collection` object is used; other keys are ignored.

### Simple maps and lists

```json
{"1234": 2, "5678": 1}
```

```json
[
  {"dbfId": 1234, "count": 2},
  {"dbfId": 5678, "ownedTotal": 1},
  {"dbfId": 4321, "normal": 1, "golden": 1}
]
```

### CSV

Needs a `dbfId` column and a count column (`ownedTotal`, `owned`, `count`, `total`, or `normal`).

```csv
dbfId,ownedTotal
1234,2
5678,1
```

## Meta decks

```json
{
  "decks": [
    {"name": "Aggro Hunter", "class": "Hunter", "tier": 1, "winrate": 54.2, "deckstring": "AAECAR8..."}
  ]
}
```

Or a plain-text file:

```text
# Aggro Hunter
AAECAR8...
# Control Warrior
AAEBAQc...
```

## How dust is computed

For each deck the script decodes the deckstring, and for every card computes
`missing = required_copies - min(owned, required_copies)`, multiplies by the rarity's
craft cost (Common 40, Rare 100, Epic 400, Legendary 1600), and sums. Core-set cards are
0 dust (uncraftable / earned by leveling) and reported separately as free cards.

## Substitute suggestions

When `--suggest-substitutes` is passed to `rank_decks.py` or `recommend_and_import.py`,
the scripts scan your owned collection for cards that could substitute for each missing
card in the recommended deck. **These are unverified, attribute-only candidates** — the
surrounding AI step is responsible for judging whether any are actually strategically
sound (curve fit, synergy tags, combo pieces).

**Flags**:
- `--suggest-substitutes` — enable substitute suggestions (default off)
- `--substitute-cost-window` — mana cost window for matching (default 1; e.g., 1 matches ±1 mana)
- `--max-substitutes` — max suggestions per missing card (default 3)

**How it works**:
1. **Legality**: candidate must be `NEUTRAL` or match the deck's class.
2. **Type match**: candidate's card type (MINION/SPELL/WEAPON) must match exactly.
3. **Mana cost window**: `|candidate_cost - missing_cost| <= window`.
4. **Copy headroom**: candidate can't already fill the deck slot (max 1 for Legendary, 2 for others).
5. **Score by overlap**: `+2` for matching race/tribe, `+1` per shared mechanic keyword.
6. Sorted by score (highest first), truncated to `--max-substitutes`.

**Important caveats**:
- Death Knight rune legality is not modeled — a Warrior-legal substitute may need the wrong rune.
- Highlander singleton rules aren't tracked — a suggested card might violate singleton constraints in that specific deck.
- Scores are based only on card attributes (cost, type, mechanics, tribe) and do not account for curve, combos, or meta positioning.

**Output**: When rendered, substitute suggestions appear as a sub-line under each missing card,
in both text reports (`format_report`, `format_visual_report`) and the Hearthstone import block
(all lines `#`-prefixed for paste safety). Example:

```
  - 1x Sample Legendary (Legendary, 1600 dust)
      owned alternatives (attribute match only, not verified): Other Legendary (9-mana Legendary)
```

**Scoping**: Substitutes are computed only for the single top-ranked (`rank_decks.py`) or
chosen (`recommend_and_import.py`) deck, and only for its first `--top-missing` entries.
This keeps the per-invocation cost low, even with large collections.

## One-shot import flow

Use `scripts/recommend_and_import.py` when you want the skills to work together:

```bash
python3 scripts/recommend_and_import.py \
  --collection collection.json \
  --decks meta_decks.json \
  --budget 4000
```

The wrapper prints the ranked report and then a `COPY THIS INTO HEARTHSTONE` block for
the chosen deck. Copy the block or just the deckstring line, open Hearthstone, create a
new deck, and accept the detected clipboard deck.

## Auto-fetching candidates

`scripts/fetch_meta_decks.py` writes a `meta_decks.json` in the shape above by
scraping deck codes from a public deck listing. It skips pages it can't parse
cleanly rather than guessing, and de-duplicates by deck code. Options:

- `--limit N` maximum decks (default 40)
- `--listing URL` override/add listing pages (repeatable)
- `--one-per-class` keep only the first deck per class for variety
- `--sleep SECONDS` delay between requests

## Visual recommendation output

`recommend_and_import.py --view visual` prints recommendation cards before the import block:

- **Best overall**: earliest/highest-priority deck from the fetched meta sample.
- **Best affordable**: strongest deck whose dust cost is within detected/provided dust.
- **Best close/easy craft**: strongest deck within `--close-dust` (default 3200).
- **Cheapest**: lowest dust cost, regardless of fetched meta order.

Use `--pick-policy` to choose which deck gets the final import block. The default is
`close`, because it balances competitiveness with being realistic to craft.
