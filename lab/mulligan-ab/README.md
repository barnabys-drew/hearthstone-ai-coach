# Mulligan A/B: Jev vs Haiku and Sonnet (offline)

The experiment behind routing mulligans and Discovers to Jev. The results and
what they mean are in [docs/jev.md](../../docs/jev.md). The tracker's
`games.db` already holds every ranked opening hand and what was actually kept,
so the mulligan can be replayed against any model with no live game and no
Power.log.

Every arm sees the state `hst live` sends Jev in a real game: the lab imports
it from `hearthstone-tracker/hstracker/jev.py`, so the experiment measures
the prompt that ships. The Claude arms live only here, in their own venv;
the tracker never imports the Anthropic SDK.

## Arms

| Arm | Model | How it is asked |
|---|---|---|
| `haiku-low` | `claude-haiku-5-5`, effort `low` | one call per hand, JSON keep count per card |
| `haiku-medium` | `claude-haiku-5-5`, effort `medium` (its default) | same |
| `haiku-4-5` | `claude-haiku-4-5`, no effort setting, no thinking | same (the coach's model until now) |
| `sonnet-5` | `claude-sonnet-5`, effort `medium` | same |
| `sonnet-5-5` | `claude-sonnet-5-5`, effort `medium` | same |
| `jev` | TypeSafe `jev-1.13.0` | one call per hand, one `noul` question per card ("keep this?"), kept at p ≥ 0.5 |

All arms see the same state: format, both classes, going first or second, the
deck list, and each offered card's cost, type, stats and rules text.
`--no-decklist` drops the deck list, to test Jev's preference for short input.
Jev is asked with `noul` instead of `choice` because Jev 1.13 favours the
first option it is given.

## Run

```bash
cd lab/mulligan-ab
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # once
.venv/bin/python mulligan_ab.py build                  # games.db -> results/hands.jsonl
.venv/bin/python mulligan_ab.py run jev --dry-run      # one prompt + a cost estimate, no calls
.venv/bin/python mulligan_ab.py run haiku-low          # needs ANTHROPIC_API_KEY
.venv/bin/python mulligan_ab.py run haiku-medium
.venv/bin/python mulligan_ab.py run haiku-4-5
.venv/bin/python mulligan_ab.py run sonnet-5
.venv/bin/python mulligan_ab.py run sonnet-5-5
.venv/bin/python mulligan_ab.py run jev                # needs TYPESAFE_API_KEY
.venv/bin/python mulligan_ab.py report
```

`run` asks each hand 3 times by default (`--repeats`), appends one line per
answer to `results/<arm>.jsonl`, and skips answers already recorded, so an
interrupted run picks up where it stopped. `--limit N` runs a smoke test.

Actual cost of the full set (64 hands × 3 repeats): $0.04 `haiku-low`,
$0.07 `haiku-medium`, $0.26 `haiku-4-5`, $0.70 `sonnet-5`, $0.67 `sonnet-5-5`,
and under a cent for each Jev arm.

## Reading the report

- `agree_pct` / `kappa`: agreement with the player's own keeps, per card
  copy. **These are not correct answers.** A model that disagrees may be
  right.
- `hand_exact_pct`: whole hands where the arm kept exactly what the player kept.
- `keep_rate_pct`: how keep-happy the arm is, next to the player's rate in
  the header.
- `stable_pct`: card decisions identical across every repeat. Jev's answers
  can change between runs.
- `auc`: Jev only. Ranks its keep probabilities against the player's keeps,
  so it doesn't depend on the 0.5 threshold.
- The confidence-interval table resamples whole hands (paired bootstrap).
  `clear` marks the differences from Jev whose 95% interval excludes zero.
  Only those count as real.
- The won/lost split is a hint at most: 64 games is a small sample.

`results/` is gitignored. It holds card names and decks from real games.
