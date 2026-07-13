# Progressive RAG: this repo as a learning lab

The live coach's lessons engine is a working retrieval-augmented system — just
not the embeddings-first kind most tutorials teach. This document names what
was built in RAG vocabulary, then lays out five phases that grow it into a
full progressive, cost-conscious RAG stack. Each phase says what it teaches
and maps it to the same pattern in a SOC context: a Tier-1 security-alert
triage agent whose knowledge base of past dispositions improves over time
without runaway cost.

**Thesis: RAG ≠ embeddings.** Retrieval starts with structure. Embeddings are
an escalation tier you adopt when measurement proves cheaper tiers miss —
not the foundation you start from.

## What we already built (named in RAG terms)

| This repo (file) | RAG concept | SOC triage equivalent |
|---|---|---|
| `lesson_store.json` + pydantic `Lesson`/`LessonTrigger` ([`hstracker/lessons.py`](../hearthstone-tracker/hstracker/lessons.py)) | Structured knowledge base with a typed metadata schema | Incident/disposition KB keyed by entities (host, user, hash, ASN) |
| `match_lessons()` — deterministic trigger matching inside `hst live` | **Tier-0 retrieval**: metadata filtering — exact, zero-cost, on the hot path | IOC/entity exact-match lookup at alert ingest |
| Lesson inlining into turn markers, capped at 3 (`live.py`) | Context assembly at decision time, under a budget | Enriching the alert with retrieved priors before the agent reasons |
| Post-game coach writing triggered records (`coach_publish.py --lesson-record`) | Offline ingestion/enrichment pipeline — LLM work stays off the hot path | Post-incident review producing structured dispositions |
| Headline record, newest wins (`headline: true`) | Periodic consolidation / distillation | Weekly playbook or threat-landscape synthesis |
| Retrieval itself never calls an LLM; LLM calls happen at boundaries (post-game, session start) | **The core cost pattern** | The dollar lever at alert volume |

The decision loop this feeds is latency-bound (~15s turn timer), which forced
the same discipline a high-volume alert queue forces: anything on the hot
path must be deterministic and effectively free; anything expensive runs at
boundaries and is cached.

## Phase 1 — Retrieval telemetry + eval harness ✅ BUILT (2026-07-12)

*The part everyone skips, and the part that matters most at work.*

Built (`hstracker/raglog.py`, `hstracker/ragreplay.py`):
- `retrieval_log.jsonl` — one event per your-turn snapshot: stable lesson ids
  (sha1 of normalized text, 12 hex chars), tiers ran, what matched (or that
  nothing did), weak game key (session + game_no) and turn. Events: `corpus`,
  `match`, `outcome`, `ingest` (new lesson recorded), `applied` (coach acked
  using a fired lesson via `coach_publish.py --applied-lesson "#<id>"`).
- Outcome joining — the live loop appends an `outcome` event at game over;
  cross-process `ingest`/`applied` events join by timestamp windows.
- `hst rag-report` — per-lesson firing rate; dead knowledge (records that
  never fire, untriggerable ones flagged); retrieval misses (games with a
  recorded misplay but zero fires — the evidence backlog that justifies or
  kills every later phase); a precision proxy (fired AND applied AND won,
  degrading to fired-AND-won when no applied events exist).
- `hst rag-replay <session-dir>` — runs the current store against historical
  Power.logs offline (reusing `LiveGameTail.snapshot`), deterministic
  `--json` output for diff-based regression tests. Never writes the live log.

First replay over a real session (13 games) already produced gate evidence:
several never-fired lessons and one lesson firing on 43 turns across 10
games — trigger specificity work before any new tier.

Teaches: retrieval evaluation, hit/miss telemetry, offline replay,
knowledge-decay detection.

SOC transfer: measure the KB before buying vector search. Replay historical
alerts through candidate retrieval configs. Find dispositions that never fire
(stale knowledge) and alerts that retrieve nothing (coverage gaps).

## Phase 2 — Tiered retrieval with escalation (lexical tier) 🧪 LAB-BUILT (2026-07-12), live-gated

**Entry gate:** Phase-1 report shows real misses — misplays where a relevant
lesson existed but its exact trigger didn't fire. *Gate not yet satisfied
(telemetry is hours old), so Tier 1 shipped as lab code: it always runs in
`rag-replay`; the live loop runs it only with `HS_RAG_T1=1`.*

Built (`hstracker/lexical.py`):
- Tier 1 runs only when Tier 0 returns nothing: pure-python BM25
  (k1=1.5, b=0.75) of lesson text against snapshot text (card names, rules
  text, flags, opponent class). Gated by score threshold AND ≥2-token
  informative overlap; headline records excluded (already always on the
  panel); trigger-less lessons ARE eligible — that's the tier's point.
- Fuzzy marker label `[T1 fuzzy]` + `#id`, so the coach weighs them below
  exact hits and can still ack with `--applied-lesson`.
- Tier usage feeds the Phase-1 log; `rag-report`/`rag-replay` show a "Tier
  earnings" section (turns each tier ran vs fired).
- Threshold tuned on session `Hearthstone_2026_07_12_04_50_10` (13 games,
  18 t0-miss turns): thresholds 2.0/5.5/6.5/7.5 fired on 18/12/8/5 miss
  turns → `SCORE_THRESHOLD = 7.5` (~28% of misses). Override per run with
  `HS_RAG_T1_MIN`; unthresholded scores via `rag-replay --candidates`.

**Acceptance evidence required to flip the live default ON** (all three):
1. ≥20 real telemetry games showing a miss backlog (`rag-report` misses, or
   dead trigger-less lessons that replay shows t1 firing on those games).
2. Replay over ≥2 sessions: user eyeballs every t1 fire (~70%+ judged
   relevant). First pass on the tuning session was MIXED — e.g. "Race swings
   go FACE, never into Lifesteal" vs Hunter looked right, but "Deny board
   vs Aura Paladin" fired vs Shaman (matchup mismatch) — so the threshold
   or query may need work before the flip. That is the lab doing its job.
3. `--tier0-only` A/B diff stays purely additive (verified once already).
If the evidence never materializes, t1 stays lab-only — also a valid outcome.

Teaches: escalation/fallback retrieval, precision-vs-recall tiering,
confidence labeling, cost gating.

SOC transfer: exact IOC match → keyword/sigma-style match → semantic search,
escalating only on miss, with evidence labeled by retrieval confidence so the
triage agent discounts fuzzier priors.

## Phase 3 — Embedding tier 🧪 LAB-BUILT (2026-07-12), live-gated

**Entry gate:** the Phase-1 report shows misses that Tier 0+1 *cannot* cover
— lessons whose relevance is semantic, not lexical ("don't overextend into
AoE" with no shared card name). If that bucket stays empty, this phase never
gets built. That is itself the lesson: eval-driven adoption.
*Gate not yet satisfied (miss bucket empty — no misplays recorded in the
telemetry games so far), so Tier 2 shipped as lab code on the Phase-2
precedent: rag-replay runs it only with `--t2` (keeps existing regression
diffs byte-stable); the live loop runs it only with `HS_RAG_T2=1`.*

Built (`hstracker/embed.py`, backend decision: **local fastembed/MiniLM** —
no second API key, keeps the no-API property; volume is nowhere near where
API pennies would teach anything):
- Embeddings computed at **write time** — each lesson embedded once when
  recorded (`append_lesson` hook, active only after `hst rag-embed`
  initializes the cache) — plus once per game (mulligan context), never per
  turn. The per-turn hot path is pure-python dot products over ≤200 cached
  unit vectors: no model, no numpy, no I/O, and fastembed itself is an
  optional dependency the read path never imports.
- Tier 2 runs only on Tier 0+1 miss: cosine top-k over the cached vectors,
  gated by `SIM_THRESHOLD` (0.48, `HS_RAG_T2_MIN` override), marker label
  `[T2 semantic]`, hits ackable with `--applied-lesson` like every tier.
- `hst rag-embed` builds/refreshes/prunes the cache (`--status` for
  coverage); a model-name mismatch invalidates every vector.
- Telemetry: t2 rows in `rag-report`/`rag-replay` tier earnings;
  `rag-replay --t2 --candidates` emits unthresholded sims for tuning.
- Threshold tuned on `Hearthstone_2026_07_12_15_31_05` (5 games, 9
  t0+t1-miss turns): plausible fires at 0.517–0.554, noise ceiling 0.446 →
  0.48 sits in the gap. First eyeball: the two ≥0.48 fires (Coin discipline
  + weapon charges, in a Coin-Rogue vs DK game) look relevant; small basis.
- A/B verified: `--t2` replay is purely additive over the default replay
  (t0/t1 events byte-identical after stripping t2 fields).

**Acceptance evidence required to flip the live default ON** (mirrors
Phase 2's bar, all three):
1. ≥20 real telemetry games whose misses are *semantic* — misplays recorded
   where replay shows t0+t1 silent but t2 firing the relevant lesson.
2. Replay over ≥2 sessions with every t2 fire eyeballed (~70%+ relevant).
3. The additive A/B property re-verified on those sessions.
If the evidence never materializes, t2 stays lab-only — also a valid outcome.

Teaches: write-time vs read-time cost asymmetry, embedding caching,
escalation economics — semantics only pays for the misses.

SOC transfer: embed dispositions at write time; embed each alert once at
ingest; only the fraction of alerts structured retrieval can't resolve ever
touches the semantic tier. At 10k alerts/day this is the difference between
a rounding error and a real bill.

## Phase 4 — Consolidation + decay (KB hygiene) ✅ BUILT (2026-07-12)

**Entry gate:** the store is big enough to have duplicates and dead weight
(Phase-1 report shows never-fired records or near-identical lessons).
*Gate satisfied: the day's report showed 10 of 17 lessons never fired.*

Built (`hstracker/hygiene.py`, `hst rag-maintain` — **dry-run by default**,
`--apply` to write):
- Per-lesson stats stamped onto the record itself (`Lesson.stats`:
  `times_fired`, `games_fired`, `games_in_corpus`, `won_when_fired`,
  `applied`, `last_fired`, `updated`) — computed from the retrieval log at
  maintenance time, never on the hot path; replay-tagged events are excluded
  so rehearsing history can't inflate real counts. Provenance now travels
  with the knowledge through every mirror.
- Near-duplicate merge: Jaccard over the same token stream Tier 1 indexes
  (threshold 0.6, `--dedupe-threshold`); records pinned to *different*
  opponent classes never merge (same words, different knowledge); the
  survivor is picked deterministically (more trigger conditions → newer →
  titled → smaller id). The loser is **archived, not deleted** —
  `lesson_archive.json` keeps every demoted record with its reason.
- Decay: unfired (any tier) across ≥15 telemetry games (`--decay-games`)
  → archived. Headlines are exempt from both merge and decay. First real
  run correctly archived nothing: the corpus is 7 games old — conservatism
  is the feature.
- Headline candidates are *reported, never auto-applied*: composing headline
  prose is the post-game coach's LLM job at a boundary; the deterministic
  pass only nominates (top repeat-firers with wins). The post-game skill now
  documents the end-of-session flow: dry-run → eyeball → `--apply` →
  synthesize a new headline from the candidates table.
- First real `--apply`: 17 lessons stamped, 0 archived; top nominee
  "Coin only when it converts" (fired 6/7 games, 3 wins, 3 acks).

Teaches: memory consolidation, TTL/decay, provenance and confidence scoring,
dedupe.

SOC transfer: disposition dedupe, stale-IOC expiry, promoting recurring
false-positive patterns into suppression rules — with the evidence attached.

## Phase 5 — Context budgeting (progressive assembly) 🧪 LAB-BUILT (2026-07-12), live-gated

**Entry gate:** retrieved context regularly exceeds what the decision needs
(more than the cap competes for the slot).
*Gate NOT satisfied at build time — measured honestly first: across 173
replayed your-turns only 2 (~1%) had more candidates than the cap
(distribution: 0×35, 1×64, 2×56, 3×16, 4×2). Built lab-gated on the
Phase-2/3 precedent; the RANKER half earns its keep today (Phase-4 stats
now say which lessons actually correlate with wins — "most conditions,
then newest" ignores that), while the BUDGET half is machinery awaiting
evidence.*

Built (`hstracker/budget.py`):
- Explicit per-decision context budget in characters (~4 chars/token;
  default 600 ≈ 150 tokens, `HS_RAG_BUDGET_CHARS` override). Tiers
  over-fetch (cap 6) and `assemble()` takes best-first until the budget or
  hard cap (5) is hit; the top candidate is always kept even over budget —
  zero context is worse than slightly too much.
- Ranking = `specificity × tier-trust × recency × confidence`: trigger
  condition count, t0 > t1 > t2, ~60-day recency decay (undated = 180
  days), and Laplace-smoothed win-rate-when-fired boosted by coach acks
  from the Phase-4 stats. Deterministic; ties break on lesson id.
- A/B through the replay harness: `rag-replay --budget --ranker
  evidence|legacy` (legacy = the pre-Phase-5 ordering, kept callable).
  First real A/B on `Hearthstone_2026_07_12_15_31_05`: the rankers genuinely
  disagree — evidence puts the 3-times-acked Coin lesson above the
  never-acked Medivh one where legacy does the reverse, and the one 4-way
  turn drops 2 lessons to fit budget.
- Telemetry: match events carry `context: {chars, dropped}` when budgeting
  ran; `rag-report` grows a "Context budget" section (avg/max chars,
  lessons dropped) — the measurement that will justify or kill a live flip.
- Live loop gated by `HS_RAG_BUDGET=1` (`HS_RAG_RANKER` picks the ranking);
  the default replay path is byte-identical to pre-Phase-5.

**Acceptance evidence required to flip the live default ON:** the gate
itself — telemetry showing candidates regularly exceeding the cap (store
growth will drive this) — plus an A/B replay showing the evidence ranker's
kept-set correlating with wins at least as well as legacy's.

Teaches: context economics, ranking under a budget, measuring the marginal
value of context.

SOC transfer: a per-alert token budget is the primary cost lever for an LLM
triage agent at volume; ranking evidence under that budget is the craft.

## Phase 6 — Coach feedback & reasoning improvement (planned as sub-phases 2026-07-12)

**Entry gate (whole phase):** Phase-1+ telemetry shows what retrieval earned.
Now measure whether the *advice given using that retrieval* was sound — did
the user follow it, did it win, was it clear?

*Orthogonal to Phases 1–5*: retrieval improves *context*; this phase improves
*generation given context*. Build only after retrieval is instrumented.

The original single-phase sketch conflated four different problems — logging
generation, detecting adherence, measuring quality, and improving generation
— each with its own gate. Split accordingly; 6a→6e is a dependency chain,
and each sub-phase repeats the lab's core move: instrument before escalating.

### 6a — Advice telemetry (instrument generation) — gate: none, telemetry always comes first

The generation-side mirror of Phase 1. Everything downstream needs it and it
costs no model calls:

- Every `coach_publish.py` publish (mulligan/turn/lethal/discover/gameover)
  gets a stable `advice_id` and appends an `advice` event to the retrieval
  log: kind, turn, headline, step count, the card names the steps reference,
  lessons cited (`--applied-lesson` acks already exist — this generalizes
  them to the whole advice record), and **the model that authored it**
  (`--model` flag the coaching skills must pass; the overlay footer shows it
  — this is where the "make the model in use visible" standing constraint
  gets implemented, and it makes cost-per-model attributable after the
  Fable credit-drain incident).
- Join key: (session, game_no, turn) — the same weak key Phase 1 uses, so
  advice events land in the same per-game aggregates.
- Deterministic, hot-path-free, dry-run-testable; `rag-report` grows an
  "Advice" section (advice events per game, kinds, models seen).

Teaches: generation-side event design, attribution (which model said what).
SOC transfer: logging every agent disposition with model/version/prompt-id —
the audit trail that makes any later quality claim checkable.

### 6b — Adherence detection (did the user follow it?) — gate: ≥N games with 6a advice events

The hard problem, done cheaply first. The Power.log already records what the
user actually played; 6a records what the coach told them to play.

- Deterministic adherence proxy: card names extracted from the advice steps
  vs cards actually played/attacked that turn (both sides visible in the
  turn's log slice). Score = overlap fraction; stored on the advice event as
  `adherence: {score, proxy: true}`. Runs offline (replay-style), never on
  the turn timer.
- Optional human label: a one-line post-game confirm ("followed the turn-8
  plan? y/n") recorded via `coach_publish.py --advice-feedback` — sparse,
  high-quality labels that calibrate the proxy (report proxy-vs-human
  agreement before trusting the proxy anywhere).
- Known limits stated up front: targets are hard to verify from names alone
  (advice "Whip into Vereesa" vs log "Whip attacked face" both mention
  Whip); the proxy measures *card-set* adherence, not *line* adherence.
  That's fine — label it as such and let 6c report both bounds.

Teaches: proxy metrics, calibrating cheap labels against sparse gold ones.
SOC transfer: "did the analyst follow the agent's recommendation" measured
from case-management logs, calibrated against occasional explicit feedback.

### 6c — Quality metrics + outcome joining — gate: adherence labels for ≥20 games

The (context, advice, followed?, won?) dataset and the report over it —
`hst coach-report` (or a rag-report section):

- Adoption: % of advice with adherence above threshold, by kind (mulligan
  advice is followed or not in one decision; turn plans partially).
- Outcome correlation: win rate when advice followed vs overridden — BOTH
  directions matter (followed-and-lost = bad advice candidates;
  overridden-and-won = the user knew better, see 6d).
- Consistency: contradiction rate — advice events for the same (game, turn)
  whose step card-sets conflict (re-advice after an UPDATE that flips the
  plan without saying why).
- Latency: advice-event ts minus turn-marker ts, distribution vs the 15s
  deadline the skill promises.
- All computed offline from the log; deterministic; replayable.

Teaches: adoption/outcome metrics, the difference between correlation and
advice quality (a coach followed into losses may be coaching hard games).
SOC transfer: disposition adoption rate, override-rate, and outcome joins —
the exact scorecard a production triage agent gets judged on.

### 6d — Failure taxonomy + KB feedback loop — gate: 6c shows a measurable bad-advice bucket

Close the loop into the knowledge base the earlier phases built:

- Classify the failure buckets from 6c: followed-and-lost (bad advice),
  overridden-and-won (user beat the coach), contradiction turns, deadline
  misses. Post-game coach reviews the worst bucket per session.
- Feed back: overridden-and-won turns are lesson candidates AUTHORED BY THE
  USER'S PLAY — nominate them to `--lesson-record` with the trigger built
  from that turn's board (the KB grows from generation failures, closing
  the loop into Phases 0–4: new lessons → trigger matching → hygiene stats).
- Nomination is deterministic; writing the lesson prose stays the post-game
  coach's LLM job at the boundary, same split as Phase 4's headlines.

Teaches: failure taxonomies, turning evaluation into training data.
SOC transfer: analyst overrides that proved correct become new dispositions
— the KB learns from the agent being wrong, with provenance.

### 6e — Generation A/B (prompt & reasoning experiments) — gate: 6c baselines stable AND 6d names a lever

Only now, with a scorecard to read, is changing the generator justified:

- A/B the coaching procedure: prompt variants, reasoning-check branches
  (e.g. "re-verify lethal math before answering"), style/length tuning.
  Assignment recorded on the 6a advice event (`variant: ...`); 6c metrics
  split by variant are the readout.
- Sample-size honesty: at ~10 advice events/game, detecting a 10-point
  adoption swing needs tens of games per arm — the report must show
  confidence, not just deltas, or this becomes vibes with extra steps.
- If no lever moves the metrics, the answer is "the generator was fine;
  invest elsewhere" — a valid outcome, same as every other gate.

Teaches: A/B testing LLM output, statistical honesty at small n,
distinguishing retrieval quality from reasoning quality.
SOC transfer: prompt changes to a triage agent ship behind an experiment
flag and win on the adoption/outcome scorecard, never on anecdotes.

## Phase 7 — Continuous verification suite (planned 2026-07-12)

**Entry gate:** more than one phase is live-gated or flipped on. At that
point "did I break an earlier tier?" stops being answerable by unit tests
alone — the stack needs tools that test all six parts against the *running*
system, on real data, repeatedly.

The distinction from the existing tests: `tests/` proves the code is right
once, at commit time; Phase 7 proves the *deployment* is still right while
it runs — the feed exporting, the tiers firing at their historical rates,
the caches fresh, the telemetry joining.

Build (when gated in):
- `hst selftest` — one command that exercises every layer end-to-end and
  prints PASS/FAIL per phase: synthetic snapshot → t0 trigger fires (P0);
  telemetry event lands and joins (P1); t0-miss snapshot → t1 scores (P2);
  cache coverage + a known-similar pair scores above threshold (P3);
  store hygiene stats within bounds (P4); context stays under budget (P5);
  advice ids join to ratings (P6). Runs against real stores/caches,
  read-only, and never writes the live telemetry log (replay's rule).
- **Drift alarms on the telemetry itself:** rag-report grows a `--check`
  mode with exit codes — match-rate collapse (t0 rate drops >X% vs its
  trailing window), dead-cache detection (lessons added but embeddings
  stale), tier silence (a tier that historically fired going quiet for N
  games), join-rate decay (applied/ingest events landing unjoined).
- **Golden-session regression:** pin 2-3 replayed sessions' `--json` output
  as fixtures; `hst selftest` diffs current replay against them so a
  retrieval change that alters history is caught before it ships (the
  additive A/B check from Phases 2/3, made permanent).
- **Live-loop watchdog surface:** the feed already prints `!!` on stale
  exports; extend to a machine-readable health line (last snapshot age,
  tiers enabled, store/cache mtimes, model name) the overlay can display —
  which also satisfies the "make the model in use visible" constraint.

Teaches: ops-grade verification of an ML-ish pipeline — the difference
between tested code and a tested *system*, canaries, drift detection,
golden-file regression.

SOC transfer: this is the monitoring stack every production triage agent
needs — retrieval health dashboards, drift alarms when dispositions stop
firing, canary alerts replayed nightly against the KB, and an auditable
"what config/model was live when this alert was triaged" trail.

## Standing constraints (apply to every phase)

Not phases of their own — invariants the whole stack must respect, recorded
here so they survive across sessions:

- **Deck tracking must stay accurate with no AI in the loop.** When the coach
  is out of credits (or simply not running), `hst live`/`hst watch` tracking,
  the overlay panels, and game recording must keep working correctly — the
  deterministic layer never depends on the LLM layer.
- **Make the model in use visible.** It should be easy to tell which AI
  model/CLI is currently coaching (e.g. surfaced in the advice payload or a
  panel footer), so behavior differences and credit burn are attributable.
- **Log every game for RAG, not just coached ones.** Telemetry and game
  capture should cover all games played — including sessions where nobody is
  actively "playing to learn" — so the eval corpus grows passively. If the
  live loop isn't running, backfill (`hst backfill`) plus replay
  (`hst rag-replay`) should be able to reconstruct the missing telemetry.

## Build order and the meta-lesson

Phase 1 first, always. Every later phase has an entry gate stated in terms of
Phase-1 evidence. Nothing gets built because it's the fashionable
architecture; everything gets built because the telemetry showed a gap it
closes. Carrying that discipline — *instrument retrieval before escalating
it* — into the work project is the whole point of this lab.
