# Jev for choice decisions: a seven-arm model comparison

The live coach makes two kinds of decision. **Turn plans** are generation: a
numbered sequence of moves, with lethal arithmetic, written for a 15-second
timer. **Mulligans and Discovers** are classification: a short fixed list of
options, each one keep or toss, pick or pass.

I had been sending both kinds to the same chat model. This page measures
whether the second kind should go somewhere else. It compares five Claude
configurations against TypeSafe's **Jev**, a model built for exactly that
shape of question, on 64 real opening hands from my own games.

**Result: Jev now answers every mulligan and Discover.** On mulligans it
landed within the error bars of the best model tested. It was the most
consistent of all seven arms, about ten times faster, and cost about 1/100
of the best Claude model. The full numbers, the method and the limits
follow, so the claim can be checked rather than taken on trust.

## Why Jev is a different kind of model

Jev doesn't write text. A request carries a plain-text `state` and a set of
typed questions, and every answer is a number:

| Question type | Answer |
|---|---|
| `noul` | probability (0–1) that a statement is true |
| `choice` | one label from a list, with a confidence and per-label probabilities |
| `score` | a position on an ordered scale |

That makes it useless for a turn plan and well suited to a mulligan: "should
the player keep Slam?" is a `noul` question, and the answer is a probability
the tracker can threshold, log and calibrate. Input costs about $0.04 per
million tokens and output is free.

Its own documentation lists its weak spots, and the integration is built
around them:

- **It leans toward whichever option is listed first** in a `choice`. So
  every decision is asked as an independent `noul` per card or per option.
  The Discover pick is the highest of those probabilities, not a `choice`
  between the options.
- **Long, noisy input hurts it.** The state is a handful of lines with
  nothing the player couldn't see on the mulligan screen.
- **It's weak at arithmetic and counting.** It's never asked to do either.
  Lethal math stays with the turn coach and the tracker's deterministic
  checks.

## The experiment

`lab/mulligan-ab/` replays the mulligan decision offline. The tracker's
`games.db` already records, for every ranked game, the opening hand and which
cards were kept. So no live game is needed, and every arm sees exactly the
same 64 hands.

- **Data:** 64 ranked opening hands from July 2026 (65 games; one dropped for
  an unusual hand size). That's 231 offered card copies, of which I kept 59.7%.
- **State, identical for every arm:** format, both classes, going first or
  second, the deck list, and each card's cost, type, stats and rules text. It
  is the same text `hst live` sends Jev in a real game; the lab imports it
  from `hstracker/jev.py`.
- **Repeats:** every hand asked three times per arm, so stability is measured
  and not assumed.
- **Claude arms:** structured JSON output, one keep count per card.
  - Haiku 5.5 at `low` and at `medium` effort.
  - Haiku 4.5, the coach's model until now, asked plain: it takes no effort
    setting, and it ran without thinking.
  - Sonnet 5 and Sonnet 5.5, both at `medium` effort, so the comparison is
    between models rather than effort levels.
- **Jev arms:** `jev-1.13.0`, one `noul` per card, kept at p ≥ 0.5. A second
  Jev arm drops the deck list.

### What it does and doesn't measure

The score is **agreement with my own keeps**. That's the only label 64 past
games provide, and **it isn't correctness.** My mulligans aren't gold labels,
and a model that disagrees with me may be right. Read the agreement column as
"plays like the player, given the same information", next to stability, speed
and cost.

## Results

All 1,344 calls (7 arms × 64 hands × 3 repeats) returned without an error.

| Arm | Agrees with my keeps | 95% CI | kappa | Keep rate | Same answer every repeat | Median latency | Cost per hand |
|---|---|---|---|---|---|---|---|
| Sonnet 5 (`medium`) | **74.0%** | 68.2–79.9 | **0.42** | 77% | 91% | 2.4s | $0.0036 |
| **Jev 1.13** | 70.9% | 64.6–77.0 | 0.35 | 77% | **96%** | **0.21s** | **$0.000035** |
| Sonnet 5.5 (`medium`) | 70.0% | 63.8–75.8 | 0.38 | 57% | 95% | 2.2s | $0.0035 |
| Haiku 5.5 (`medium`) | 69.7% | 63.9–75.9 | 0.32 | 79% | 82% | 3.6s | $0.00038 |
| Haiku 4.5 | 67.8% | 61.8–73.9 | 0.33 | 61% | 85% | 2.2s | $0.0013 |
| Haiku 5.5 (`low`) | 66.1% | 59.8–72.3 | 0.24 | 78% | 89% | 1.8s | $0.00021 |
| Jev, no deck list | 63.3% | 57.0–69.6 | 0.25 | 57% | 90% | 0.20s | $0.000028 |

Confidence intervals and the comparisons below come from a paired bootstrap
over hands: whole hands resampled 2,000 times, since cards within a hand are
not independent. The whole comparison cost about $1.75 in API calls; Jev's
share was under a cent.

### The comparisons that are and aren't real

| Comparison | Difference | 95% CI | Real? |
|---|---|---|---|
| Sonnet 5 − Jev | +3.1 | −1.6 to +8.2 | no: inside the noise |
| Sonnet 5.5 − Jev | −0.9 | −7.0 to +5.3 | no |
| Haiku 5.5 `medium` − Jev | −1.2 | −5.7 to +3.6 | no |
| Haiku 4.5 − Jev | −3.1 | −8.6 to +2.2 | no |
| Jev without deck list − Jev | −7.6 | −13.2 to −2.2 | **yes** |
| Sonnet 5 − Haiku 5.5 `medium` | +4.3 | +1.4 to +7.3 | **yes** |
| Sonnet 5 − Haiku 4.5 | +6.2 | +0.6 to +12.3 | **yes** |
| Haiku 5.5 `medium` − Haiku 5.5 `low` | +3.6 | +0.3 to +7.0 | **yes** |
| Haiku 5.5 `medium` − Haiku 4.5 | +1.9 | −3.9 to +8.3 | no |
| Sonnet 5 − Sonnet 5.5 | +4.0 | −1.8 to +9.9 | no |

## What the comparison says

**Sonnet 5 is the only model that clearly separates from the pack.** It beats
both Haikus outright, and it sits about three points above Jev. With 64
hands, three points is something the data can't distinguish from zero. If
agreement were the only thing that mattered, Sonnet 5 would be the pick.

**On this task, agreement isn't the only thing that matters.**

- **Speed:** a mulligan screen gives you seconds. Jev answers in 0.2s, so the
  card is on the overlay about a second after the hand is dealt (the tracker
  polls the log once a second). The coaching loop through a chat session had
  a 23.6s median for any advice at all.
- **Stability:** Jev returned the same answer on 96% of card decisions,
  against 82% for Haiku 5.5. A coach that gives a different mulligan for the
  same hand is hard to trust and hard to measure.
- **Cost:** at $0.000035 a hand, a thousand mulligans cost 3.5 cents.
  Sonnet 5 costs a hundred times that, and its reasoning budget is better
  spent on turn plans.

So within the error bars Jev ties every model tested, including the best one,
at a tenth of the latency and a hundredth of the cost. That's the case for
it, and it's a strong one for a decision of this shape.

**Other things the numbers show:**

- **Context matters more than input length.** Jev's docs say shorter input
  is better. Removing the deck list cost it 7.6 points, a real drop, and made
  it throw back far more (57% keep rate). With no deck list it can't judge
  curve or synergy, so it guesses conservatively. The deck list stays.
- **Effort matters on Haiku 5.5.** `low` was clearly worse than `medium`.
  "Use `low` to make the coach faster" is a trade, not a free win.
- **Haiku 5.5 isn't measurably better than Haiku 4.5 here.** It's +1.9
  points with an interval that spans zero. It is about 3.5x cheaper per hand
  at `medium` and 6x cheaper at `low`, and it's the newer model, but on
  mulligans the quality jump doesn't show up in 64 hands.
- **Sonnet 5.5 mulligans like me in volume, not in choices.** It keeps 57%
  of cards, the closest of any arm to my 60%. But it agrees with me on which
  cards only 70% of the time, so it throws back different cards than I do.
  Haiku 4.5's keep rate (61%) is similarly close to mine.
- **Most disagreement points one way.** Haiku 5.5, Jev and Sonnet 5 keep
  77–79% of cards. On the 47 card decisions where Haiku 5.5 and Jev both
  disagreed with me, 43 were cards I threw back and both kept: Preparation,
  Torch, Lotus Troublemaker, Fan of Knives and similar situational cards.
  Whether my harder mulligans are right is the open question. HSReplay keep
  rates for those cards would settle it.

## How it runs in the coach

`hearthstone-tracker/hstracker/jev.py`, enabled with `HS_JEV=1` plus
`TYPESAFE_API_KEY`:

1. `hst live` sees a mulligan-phase snapshot or a new pending Discover.
2. It builds the short state (the tested text, word for word for ranked
   games) and asks Jev **on a background thread**. The live loop never waits
   on the network.
3. The answer is published to the overlay: a `kind=mulligan` card with each
   card's keep probability, or a Discover pick merged into the current advice
   card. The panel footer shows `jev-1.13.0`.
4. An `advice` event with `model: jev-1.13.0`, `variant: jev`, the
   probabilities, latency, tokens and cost lands in the retrieval log, so
   `hst coach-report` attributes it like any other advice.
5. A `== JEV MULLIGAN` / `== JEV DISCOVER` line goes to the feed, so the turn
   coach knows the decision is made and doesn't redo it.

**Failure is boring on purpose.** A missing key, a timeout or an HTTP error
prints one `!! Jev ... failed` line, and the coach model answers that
decision the old way. Deck tracking, the overlay panels and game recording
never depend on Jev, the same rule that already protects them from the turn
coach running out of credits. `hst selftest` exercises the whole Jev path
against a fake API (no network, no spend) and reports whether it's on.

The model is pinned (`jev-1.13.0`). `jev-latest` would change the answers
silently, and every number on this page would stop describing what runs.

## Limits, stated plainly

- **64 hands is a small sample.** It's enough to show Sonnet 5 above the
  Haikus and enough to show the deck list matters. It isn't enough to rank
  Jev, Sonnet 5.5 and Haiku 5.5 against each other.
- **Agreement isn't correctness**, as above.
- **Discover picks aren't measured yet.** They use the same `noul` design,
  and every live pick is logged with its probabilities so it can be scored
  against what I actually picked and how the game went. Until that data
  exists, the Discover pick is a reasoned extension of the mulligan result,
  not a measured one.
- **Turn plans stay with a generative model.** Nothing here suggests Jev can
  write one, and its docs say it shouldn't try.

## Reproduce it

```bash
cd lab/mulligan-ab
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python mulligan_ab.py build          # your games.db -> results/hands.jsonl
.venv/bin/python mulligan_ab.py run jev --dry-run
.venv/bin/python mulligan_ab.py run jev        # TYPESAFE_API_KEY
.venv/bin/python mulligan_ab.py run sonnet-5   # ANTHROPIC_API_KEY; also haiku-low, haiku-medium, haiku-4-5, sonnet-5-5
.venv/bin/python mulligan_ab.py report
```

Runs append one line per answer and resume where they stopped, and the report
prints every table on this page. See [`lab/mulligan-ab/README.md`](../lab/mulligan-ab/README.md).

## The transferable lesson

Route each decision to the cheapest model that's built for its shape, and
measure before you route. A chat model is the general tool. A classifier
that answers in 0.2s for a hundredth of the price is the right tool for any
decision that is really "which of these few options". In this repo's other
domain, that's the difference between triaging an alert queue with a chat
agent and sending the classification-shaped part to a classifier (see the
Phase 6e entry in [progressive-rag.md](progressive-rag.md)).
