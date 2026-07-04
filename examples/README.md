# Examples

These are deterministic sample fixtures used by the README and tests. They use tiny synthetic DBF IDs so the repository can be tested without network access or real account data.

They are **not** real playable Hearthstone decks.

Try:

```bash
python3 ../hearthstone-deck-recommender/scripts/recommend_and_import.py \
  --collection collection.sample.json \
  --decks meta_decks.sample.json \
  --cards-json cards.sample.json \
  --no-fetch
```
