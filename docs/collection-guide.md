# Collection guide

The recommender needs one thing that Hearthstone itself does not expose through an official public API: your owned card collection.

This guide explains the supported ways to get that data into `rank_decks.py` or `recommend_and_import.py`.

## Option 1: HSReplay collection JSON

This is usually the best route if you already use Hearthstone Deck Tracker or another tracker that syncs to HSReplay.

High-level flow:

1. Sync your collection through your tracker.
2. Open your collection page on HSReplay while signed in.
3. Open browser DevTools → Network.
4. Reload the page.
5. Find the JSON request whose URL contains `account_lo=`.
6. Copy the JSON response and save it as `collection.json`.

The expected shape is similar to:

```json
{
  "collection": {
    "1234": [2, 0, 0, 0],
    "5678": [0, 1, 0, 0]
  }
}
```

The four-number array represents finishes such as normal/golden/diamond/signature. The script sums them because any finish satisfies a deck slot.

If the request URL itself returns JSON, you can pass it directly:

```bash
python3 hearthstone-deck-recommender/scripts/recommend_and_import.py \
  --collection-url "https://...account_lo=..." \
  --decks meta_decks.json
```

If the URL only works inside your browser session, save the JSON response to a file instead. `--collection-cookie` exists for advanced local use, but avoid putting cookies in shell history, commits, screenshots, or bug reports.

## Option 2: Deck-tracker export

Exports from Hearthstone Deck Tracker, Firestone, or similar tools work if they include DBF IDs and counts.

Supported JSON shapes include:

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

Supported CSV shape:

```csv
dbfId,ownedTotal
1234,2
5678,1
```

Run:

```bash
python3 hearthstone-deck-recommender/scripts/rank_decks.py \
  --collection collection.csv \
  --decks meta_decks.json
```

## Option 3: Manual mini-collection

For testing or partial recommendations, you can hand-write a small map of DBF IDs:

```json
{
  "1234": 2,
  "5678": 1
}
```

This is less accurate but useful for quick experiments.

## Privacy checklist

Before making issues, examples, or screenshots public, remove:

- real `collection.json` files,
- `account_lo` URLs,
- Cookie headers,
- account IDs,
- screenshots of logged-in collection pages.

The repository `.gitignore` excludes common local collection and deck-data filenames, but still review `git diff --cached` before pushing.
