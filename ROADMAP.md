# Roadmap

Each item is a GitHub issue; this page groups them and says what order they
go in. The retrieval side (the lessons engine as a progressive-RAG lab) has
its own phase-by-phase plan in [docs/progressive-rag.md](docs/progressive-rag.md).

The rule from that plan applies here too: **measure before building.** Most
items below are gated on real games, because the stack now logs everything
needed to answer them. The fastest way to move this roadmap is to play
sessions with the overlay's choices picker on **Both**.

## Next: measure what's already logged

| Issue | What it settles |
|---|---|
| [#20](https://github.com/barnabys-drew/hearthstone-ai-coach/issues/20) | Both-mode readout: coach vs Jev vs the cards actually kept, per game |
| [#19](https://github.com/barnabys-drew/hearthstone-ai-coach/issues/19) | Score Jev's Discover picks (logged, not yet measured) |
| [#21](https://github.com/barnabys-drew/hearthstone-ai-coach/issues/21) | Who's right on disputed mulligans: the player or the models? |
| [#22](https://github.com/barnabys-drew/hearthstone-ai-coach/issues/22) | Rerun the [model comparison](docs/jev.md) on a larger sample |

## Coach quality and speed

| Issue | What it settles |
|---|---|
| [#23](https://github.com/barnabys-drew/hearthstone-ai-coach/issues/23) | Check gate on by default? Gated vs full over real games |
| [#24](https://github.com/barnabys-drew/hearthstone-ai-coach/issues/24) | Turn-plan latency: ~24s median against a 15s timer |
| [#26](https://github.com/barnabys-drew/hearthstone-ai-coach/issues/26) | Flip fuzzy/semantic lesson retrieval live, or keep it lab-only |

## Maintenance

| Issue | What it settles |
|---|---|
| [#29](https://github.com/barnabys-drew/hearthstone-ai-coach/issues/29) | Confirm Electron 44 on Windows |
| [#27](https://github.com/barnabys-drew/hearthstone-ai-coach/issues/27) | Move to ruff 0.17's rules and unpin it |

## Ideas (design first)

| Issue | Idea |
|---|---|
| [#30](https://github.com/barnabys-drew/hearthstone-ai-coach/issues/30) | Let the overlay choose the turn-plan model (needs a spending guard first) |

## Done recently

- **Turn-plan validator (#25):** every turn and lethal publish is checked
  against the live board (taunts, mana, cards that exist nowhere, Reborn),
  with warnings sent back to the coach and onto the overlay.
- **`hst overlay-sync` (#28):** keeps the Windows Electron copy current;
  `hst selftest` warns when it drifts.
- **Mulligan and Discover readouts in `hst coach-report`** (#20, #19): built,
  and waiting on Both-mode games to fill them.
- Jev answers mulligans and Discovers, chosen by a seven-model comparison
  ([docs/jev.md](docs/jev.md)).
- An overlay picker chooses who answers them: Jev, Coach, Both or Off.
- The per-turn check gate (`HS_COACH_GATED=1`).
- CI green again: ruff pinned, the selftest renderer check fixed, and the
  selftest unit test sandboxed.
- Ten code-review fixes, including lethal counting for "minion or hero"
  damage.
