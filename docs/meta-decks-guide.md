# Meta decks guide

The recommender is intentionally deterministic: it does not hard-code a meta tier list. Instead, an AI agent or a human provides a current set of deckstrings, and the script ranks those deckstrings against a collection.

This keeps the project stable while Hearthstone balance patches, expansions, rotations, and mini-sets change the actual meta.

## Recommended input shape

Save current candidate decks as `meta_decks.json`:

```json
{
  "decks": [
    {
      "name": "Aggro Hunter",
      "class": "Hunter",
      "format": "standard",
      "tier": 1,
      "winrate": 54.2,
      "source": "HSReplay / HSGuru / Vicious Syndicate / Hearthstone Top Decks / d0nkey",
      "deckstring": "AAECAR8..."
    }
  ]
}
```

Only `name` and `deckstring` are strictly required. `class`, `format`, `tier`, `winrate`, and `source` improve the final recommendation.

## Plain-text input

A simpler text format also works:

```text
# Aggro Hunter
AAECAR8...
# Control Warrior
AAEBAQc...
```

Run:

```bash
python3 hearthstone-deck-recommender/scripts/rank_decks.py \
  --collection collection.json \
  --decks meta_decks.txt
```

## Choosing sources

Good candidate sources are sites that publish importable deck codes and current Standard data. Examples include HSReplay, HSGuru, Vicious Syndicate, d0nkey, and Hearthstone Top Decks.

When an AI agent uses this repository, it should browse current sources, extract deckstrings, and save them in the JSON shape above before invoking the scripts.

## Recommendation heuristic

The script ranks by:

1. lowest dust needed,
2. highest win rate as a tiebreaker when available.

A human or AI agent should still apply judgment. For example, a 0-dust tier-3 deck may be less useful than a 1,200-dust tier-1 deck if the user wants serious ladder performance.
