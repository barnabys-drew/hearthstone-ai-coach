# Hearthstone Dust Optimizer — Quick Start

Built: July 2026  
Skill: `/hearthstone-dust-optimizer`

## Overview

The Dust Optimizer helps you:
1. **Analyze your collection** — identify cards worth disenchanting
2. **Rank recommendations** — by priority, dust value, and meta relevance
3. **Automate deletion** — use Playwright to disenchant cards in Hearthstone UI

Perfect for pivoting to a new deck or freeing up dust quickly.

## Basic Usage

### 1. Analyze Your Collection (No Changes)

```bash
cd hearthstone-deck-recommender

python3 scripts/optimize_dust.py \
  --collection ../path/to/collection.json \
  --view summary
```

This prints recommendations without touching anything.

### 2. Save Recommendations to File

```bash
python3 scripts/optimize_dust.py \
  --collection ../path/to/collection.json \
  --decks meta_decks.json \
  --output recommendations.json
```

Now you have a JSON file you can review or share.

### 3. Preview with Automation (Dry-Run)

```bash
python3 scripts/optimize_dust.py \
  --collection ../path/to/collection.json \
  --automate \
  --dry-run true \
  --headless false
```

Shows what WOULD be deleted without actually deleting anything. Browser stays open so you can see the UI.

### 4. Actually Delete Cards

```bash
python3 scripts/optimize_dust.py \
  --collection ../path/to/collection.json \
  --automate \
  --dry-run false \
  --headless false
```

Browser opens, finds each card, and disenchants it. Watch the screen to verify.

## Command Flags

| Flag | Purpose | Default |
|------|---------|---------|
| `--collection PATH` | Path to collection JSON | **required** |
| `--decks PATH` | Meta decks JSON (for relevance) | optional |
| `--cards-json PATH` | Card database JSON | auto-fetch |
| `--view` | Output format: summary/detailed/json | summary |
| `--automate` | Enable Playwright automation | off |
| `--dry-run` | Preview only, don't delete | true |
| `--headless` | Run browser headless | true |
| `--threshold` | Minimum dust to consider | 40 |
| `--output` | Save results to JSON | optional |

## Workflow Example

### Scenario: Pivoting from Aya Rogue to a new deck

```bash
# Step 1: See what you can disenchant
python3 scripts/optimize_dust.py \
  --collection ~/.local/share/hearthstone-tracker/collection.json \
  --view summary

# Step 2: Review recommendations (printed to screen)
# Output shows: 560 dust available, 5 high-priority cards, etc.

# Step 3: Save for reference
python3 scripts/optimize_dust.py \
  --collection ~/.local/share/hearthstone-tracker/collection.json \
  --output my_disenchants.json

# Step 4: Preview automation (dry-run)
python3 scripts/optimize_dust.py \
  --collection ~/.local/share/hearthstone-tracker/collection.json \
  --automate \
  --dry-run true \
  --headless false

# Step 5: If satisfied, actually delete
python3 scripts/optimize_dust.py \
  --collection ~/.local/share/hearthstone-tracker/collection.json \
  --automate \
  --dry-run false \
  --headless false
```

## Important Notes

### Before You Disenchant

1. **Review recommendations manually** — the tool makes smart guesses, but you know your decks best
2. **Don't disenchant cards you plan to use** — check future decks you're thinking of building
3. **Golden copies first** — automation prioritizes golden/premium versions (same dust, less regret)
4. **One expansion at a time** — if you're nervous, start with just the oldest expansion's trash

### Playwright Setup

Automation requires Playwright:

```bash
pip install playwright
playwright install chromium
```

### Collection Format

The tool accepts:
- **HSReplay JSON** — your exported collection from hsreplay.net (auto-detected)
- **Simple map** — `{dbfId: count}` JSON object
- **Heroku-compatible** — single-account or multi-account HSReplay exports

### Limitations

- **Doesn't understand meta shifts** — uses current meta only
- **Assumes max copies** — keeps 2 of everything (1 if Legendary), adjust manually if you want more
- **Automation is UI-fragile** — if Blizzard changes the collection UI, selectors need updating
- **Account-specific** — must run while logged into the Hearthstone account you want to disenchant from

## Troubleshooting

### "Playwright not installed"

```bash
pip install playwright
playwright install chromium
```

### "Could not find search box" / "Disenchant button not found"

Blizzard may have updated the UI. You can:
1. File an issue with screenshots
2. Manually find the correct selectors in browser DevTools
3. Edit `hearthstone_disenchant_automation.py` and update the selectors

### "Collection not found"

Verify your path:
```bash
ls -la ~/.local/share/hearthstone-tracker/collection.json
```

If missing, export from HSReplay or your deck tracker.

### Automation hangs

The browser might be waiting for login. Make sure you're logged into Battle.net before running with `--automate`.

## Example Output

```
DISENCHANT RECOMMENDATIONS
========================================

HIGH PRIORITY:
  - 2x Old Card (Common, 5 dust each = 10 total)
      reason: duplicate_copies
  - 3x Outdated Spell (Rare, 20 dust each = 60 total)
      reason: duplicate_copies

MEDIUM PRIORITY:
  - 1x Niche Minion (Epic, 100 dust)
      reason: low_meta_relevance

TOTAL DUST AVAILABLE: 170
```

## See Also

- `/hearthstone-deck-recommender` — Pick your next deck
- `/hearthstone-deck-builder` — Build it after freeing up dust
- `CLAUDE.md` — Project configuration
