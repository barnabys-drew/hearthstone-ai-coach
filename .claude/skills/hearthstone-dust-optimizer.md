---
name: hearthstone-dust-optimizer
description: Analyze your collection and recommend cards to disenchant, with optional automated deletion via Playwright
---

# Hearthstone Dust Optimizer

Evaluate your card collection and intelligently recommend which cards to disenchant based on rotation status, meta relevance, and deck necessity. Then optionally automate the deletion process in Hearthstone using Playwright.

## When to Use

Use this skill when:
- You want to free up dust for a new deck but aren't sure which cards are safe to cut
- You need to rotate out cards approaching Standard rotation
- You want to identify duplicate copies or low-value cards
- You're pivoting away from a deck archetype and need to liquidate assets

## How It Works

The optimizer evaluates cards by:

1. **Rotation status** — cards rotating out soon get higher disenchant priority
2. **Meta relevance** — cards not in any current competitive deck get flagged
3. **Deck dependency** — cards essential to active deck lists get protected
4. **Copy count** — duplicate copies (3rd+ of a card) get marked for disenchant
5. **Dust value** — sorts recommendations by total dust gain (rarity × count)

Outputs a ranked list of disenchant candidates with reasoning, total dust gain, and a confirmation step before automation.

## Command-Line Usage

```bash
# Analyze collection and recommend disenchants (no deletion)
python3 hearthstone-deck-recommender/scripts/optimize_dust.py \
  --collection collection.json \
  --cards-json cards.collectible.json \
  --decks meta_decks.json \
  --output report.json

# Analyze + show summary
python3 hearthstone-deck-recommender/scripts/optimize_dust.py \
  --collection collection.json \
  --decks meta_decks.json \
  --view summary

# Automate deletion (requires user approval + Playwright)
python3 hearthstone-deck-recommender/scripts/optimize_dust.py \
  --collection collection.json \
  --decks meta_decks.json \
  --automate \
  --headless false  # show browser during deletion
```

## Automation (Playwright)

Once you've approved the disenchant list, the tool can:
1. Launch Hearthstone (via Battle.net or direct executable)
2. Navigate to the collection screen
3. Find each card by name/rarity filter
4. Click the disenchant button
5. Confirm deletion in the UI
6. Log successful disenchants + dust gained

**Prerequisites for automation:**
- Hearthstone client running or ability to launch it
- Playwright installed (`pip install playwright`)
- Browser drivers installed (`playwright install chromium`)
- User confirmation of the disenchant list

**Safety features:**
- Dry-run mode (default) — shows what would be deleted without actually doing it
- Confirmation step — user must approve before any deletion
- Logging — all actions logged with timestamps
- Reversible — Hearthstone allows re-crafting cards (at higher dust cost)

## Output Format

Text summary:
```
DISENCHANT RECOMMENDATIONS
==========================

HIGH PRIORITY (rotating out soon):
  - 2x Old Expansion Card (Common, 40 dust each = 80 total)
      reason: rotation candidate

MEDIUM PRIORITY (low meta relevance):
  - 1x Niche Spell (Rare, 100 dust)
      reason: not in top 100 decks
  - 3x Duplicate Minion (Common, 40 dust each = 80 total)
      reason: more than max copies needed

LOW PRIORITY (situational):
  - 1x Situational Tech (Epic, 400 dust)
      reason: specific matchup counter, low play rate

TOTAL DUST AVAILABLE: 560
```

JSON output:
```json
{
  "recommendations": [
    {
      "card_id": 12345,
      "name": "Old Expansion Card",
      "count": 2,
      "rarity": "COMMON",
      "dust_per_copy": 40,
      "total_dust": 80,
      "reason": "rotation_candidate",
      "priority": "high"
    }
  ],
  "total_dust_available": 560,
  "approval_status": "pending"
}
```

## Flags

- `--collection PATH` — path to collection JSON (required)
- `--decks PATH` — optional; ranks disenchants by meta relevance
- `--cards-json PATH` — card data (fetched automatically if omitted)
- `--view summary|detailed|json` — output format (default: summary)
- `--automate` — enable Playwright automation (requires approval)
- `--headless true|false` — run browser in headless mode (default: true)
- `--dry-run` — show what would be deleted without actually deleting (default: true)
- `--threshold N` — only suggest cards worth N+ dust (default: 40)
- `--exclude-decks PATH` — protect cards used in specific decks (JSON list of deckstrings)

## Example Workflow

```bash
# 1. Analyze without changes
python3 optimize_dust.py --collection collection.json --view summary

# 2. Review recommendations (printed to console)
# 3. If happy, enable automation
python3 optimize_dust.py --collection collection.json --automate --dry-run false

# 4. Browser opens, shows each card, waits for user to confirm/skip
# 5. Automation proceeds, logs results
# 6. Summary printed with actual dust gained
```

## Important Caveats

**This tool does NOT:**
- Guarantee meta-optimal decisions (you may want to keep cards for fun)
- Know about future expansions (can't predict new meta-shifting cards)
- Understand your personal play preferences (protect high-value cards manually if needed)
- Handle Death Knight class-specific rules or Rune legality

**Before disenchanting:**
- Review the recommendation summary manually
- Verify cards aren't needed for a future deck you're planning
- Check if you're close to crafting a key card (might be better to wait)
- Consider keeping 1-2 copies of versatile neutral cards (even low-meta, good as fillers)

**Golden/Signature copies:**
- The tool defaults to disenchanting golden copies first (same dust value as regular)
- You can configure to prefer regular copies instead

## See Also

- `/hearthstone-deck-recommender` — pick your next deck
- `/hearthstone-substitute-suggester` — find budget alternatives
- `hearthstone-deck-builder` — import and build the chosen deck
