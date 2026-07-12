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

## Phase 1 — Retrieval telemetry + eval harness

*The part everyone skips, and the part that matters most at work.*

Build:
- `retrieval_log.jsonl` — one event per your-turn snapshot: stable lesson ids
  (hash of lesson text), which tiers ran, what matched, game id and turn.
- Outcome joining — post-game, attach the game result and whether the coach
  actually applied each fired lesson.
- `hst rag-report` — per-lesson firing rate; dead knowledge (records that
  never fire); retrieval misses (turns with misplays but zero matches — the
  evidence backlog that justifies or kills every later phase); a precision
  proxy (fired AND applied AND game won).
- `hst rag-replay <session-dir>` — run the current store against historical
  Power.logs offline (reusing `LiveGameTail`), so any retrieval change can be
  regression-tested against real past games before it goes live.

Teaches: retrieval evaluation, hit/miss telemetry, offline replay,
knowledge-decay detection.

SOC transfer: measure the KB before buying vector search. Replay historical
alerts through candidate retrieval configs. Find dispositions that never fire
(stale knowledge) and alerts that retrieve nothing (coverage gaps).

## Phase 2 — Tiered retrieval with escalation (lexical tier)

**Entry gate:** Phase-1 report shows real misses — misplays where a relevant
lesson existed but its exact trigger didn't fire.

Build:
- Tier 1 runs only when Tier 0 returns nothing: pure-python BM25-style
  lexical scoring of lesson text against snapshot text (card names, rules
  text, opponent class). Threshold-gated to stay quiet.
- Fuzzy matches are labeled in the marker (`[T1 fuzzy]`) so the coach can
  weigh them below exact hits.
- Tier usage feeds the Phase-1 log, so the report shows what each tier earns.

Teaches: escalation/fallback retrieval, precision-vs-recall tiering,
confidence labeling, cost gating.

SOC transfer: exact IOC match → keyword/sigma-style match → semantic search,
escalating only on miss, with evidence labeled by retrieval confidence so the
triage agent discounts fuzzier priors.

## Phase 3 — Embedding tier (deferred until data justifies)

**Entry gate:** the Phase-1 report shows misses that Tier 0+1 *cannot* cover
— lessons whose relevance is semantic, not lexical ("don't overextend into
AoE" with no shared card name). If that bucket stays empty, this phase never
gets built. That is itself the lesson: eval-driven adoption.

Build (when gated in):
- Embeddings computed at **write time** — each lesson embedded once when
  recorded — plus once per game (mulligan context), never per turn.
- Tier 2 runs only on Tier 0+1 miss: cosine top-k over the cached vectors.
- Local model (fastembed/ONNX MiniLM; zero per-call cost, keeps the repo's
  no-API property) vs API embeddings (pennies, teaches key/cost management)
  decided at build time with Phase-1 evidence in hand.

Teaches: write-time vs read-time cost asymmetry, embedding caching,
escalation economics — semantics only pays for the misses.

SOC transfer: embed dispositions at write time; embed each alert once at
ingest; only the fraction of alerts structured retrieval can't resolve ever
touches the semantic tier. At 10k alerts/day this is the difference between
a rounding error and a real bill.

## Phase 4 — Consolidation + decay (KB hygiene)

**Entry gate:** the store is big enough to have duplicates and dead weight
(Phase-1 report shows never-fired records or near-identical lessons).

Build:
- Post-game maintenance pass: merge near-duplicate lessons (lexical
  similarity), archive records that haven't fired in N games (decay),
  promote repeat-firers toward the headline.
- Per-lesson stats on the record itself: `times_fired`, `last_fired`,
  win correlation — provenance and confidence travel with the knowledge.

Teaches: memory consolidation, TTL/decay, provenance and confidence scoring,
dedupe.

SOC transfer: disposition dedupe, stale-IOC expiry, promoting recurring
false-positive patterns into suppression rules — with the evidence attached.

## Phase 5 — Context budgeting (progressive assembly)

**Entry gate:** retrieved context regularly exceeds what the decision needs
(more than the cap competes for the slot).

Build:
- An explicit token budget for retrieved context per decision; rank candidates
  by `specificity × recency × confidence` (Phase-4 stats); truncate to budget.
- A/B ranking functions through the Phase-1 replay harness — measure whether
  more context actually improves outcomes, or just costs more.

Teaches: context economics, ranking under a budget, measuring the marginal
value of context.

SOC transfer: a per-alert token budget is the primary cost lever for an LLM
triage agent at volume; ranking evidence under that budget is the craft.

## Build order and the meta-lesson

Phase 1 first, always. Every later phase has an entry gate stated in terms of
Phase-1 evidence. Nothing gets built because it's the fashionable
architecture; everything gets built because the telemetry showed a gap it
closes. Carrying that discipline — *instrument retrieval before escalating
it* — into the work project is the whole point of this lab.
