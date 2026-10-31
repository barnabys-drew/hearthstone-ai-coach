---
name: hearthstone-live-coach
description: Coach the user through a live Hearthstone game in real time by tailing the game's own logs. Use this when the user asks for live/in-game Hearthstone advice, turn-by-turn coaching, mulligan help while playing, "what's my play", or lethal checks during a match. Requires the hearthstone-tracker CLI from this repository and a running Hearthstone client with Power logging enabled.
---

# Hearthstone Live Coach

## COST GUARD — check BEFORE arming anything (hard rule, 2026-07-12)

Coaching fires dozens of model invocations per game. A Fable-tier coaching
session once drained the API key mid-game and killed the chat. Before starting
the feed or arming the Monitor, check BOTH:

1. **Model check:** the system prompt names the model powering you. If it is
   Fable, Opus, or any highest-tier model, REFUSE to coach. Tell the user to
   switch first (e.g. `/model haiku`) — Haiku-class is the coaching tier.
2. **Session-name check:** if the chat/session has been renamed and its title
   is visible to you and contains `dev`, `developer`, or `development`, this
   is a development session (very likely running Fable or the highest
   available tier on a subscription) — REFUSE to coach regardless of model.

If coaching is already armed when the model switches to a top tier (the user runs
`/model` for dev work mid-session), STOP the coaching Monitor immediately and
publish a `--kind idle "Coach offline (dev mode)"` card so the overlay isn't
stale. The deterministic layer (feed, deck tracker panels) keeps running — it
never touches the model.

Give play-by-play advice during a real Hearthstone game. The `hst live` command
(in `../hearthstone-tracker/`) tails the game's Power.log and publishes the full
visible game state each turn; you read that state and advise. Everything here is
legal-information coaching: the log only contains what the player's client is
allowed to see (the opponent's hand appears only as a hidden-card count).

This skill is deliberately procedural and model-agnostic: any AI agent that
can run shell commands and follow this playbook can coach (see
`docs/coach-with-any-model.md`). With Jev switched on, the two choice
decisions (mulligan and Discover) are answered by the tracker itself through
TypeSafe's Jev classifier, and you coach the turns — see "Mulligans and
Discovers: Jev answers them" below and `docs/jev.md`. Follow it exactly even if you believe you
know the game well — several of the rules below exist because a capable model
got them wrong in live play.

## Lessons: remember misplays across games

Misplays are stored in TWO places, and both matter:

1. **`lesson_store.json`** (structured, trigger-matched) — records with
   trigger conditions that `hst live` matches against every your-turn
   snapshot and inlines into the marker as `lessons matched (N)` lines.
   THESE LINES ARE THE HIGHEST-PRIORITY INPUT ON A TURN: they are past
   mistakes that apply to the exact board in front of you — apply them or
   explicitly say why not. Record new ones with
   `coach_publish.py --lesson-record` (see the overlay publishing section).
   A line prefixed `[T1 fuzzy]` is a text-similarity match, not an exact
   trigger hit — treat it as a suggestion to weigh, below exact matches;
   ignore it freely if it doesn't fit the board.
2. **`lessons.md`** (prose, human-readable) — the narrative log below.

## Lessons file: remember misplays across games

`~/.local/share/hearthstone-tracker/lessons.md` is a running, dated list of
misplays and sequencing mistakes flagged in past games — either by you or by
the user pointing one out after the fact. It persists across sessions so the
same mistake doesn't repeat game after game.

- **At the start of every session, before the mulligan advice:** read this
  file (create it with a one-line header if it doesn't exist yet). Skim for
  anything relevant to the current class/matchup and fold it into your
  mulligan/turn-1 advice where it applies — don't just recite the whole file
  at the user.
- **Whenever a misplay comes up** — you catch one in the moment, the user
  points one out after playing a turn differently than advised, or a
  post-game review surfaces one — append a new dated entry: matchup, what
  happened, the better line, in 2-4 lines. Newest entries at the top.
- This file is for *play mistakes and sequencing lessons*, not general card
  reference (that's what `deck_cards_left`/rules text in the snapshot are
  for) and not deck-building feedback (that belongs to the deck-cleaner
  skill's own output, not here).

## Setup (once per session)

1. Start the feed via the launcher — NEVER by running `./hst live` directly:
   ```bash
   HS_JEV=1 HS_COACH_GATED=1 <repo>/hearthstone-tracker/coach_feed.sh
   ```
   Who answers mulligans and Discovers is picked in the overlay (the choices
   button: Jev / Coach / Both / Off). `HS_JEV=1` only sets the default for
   when nothing has been picked yet; the Jev modes need `TYPESAFE_API_KEY`
   exported. See "Mulligans and Discovers: follow the choices mode" below.
   It kills any zombie feed from previous sessions (detached children outlive
   their shell wrappers and TaskStop — a 2-day-old zombie once fed a whole
   session through a corrupt shared log), starts exactly ONE feed appending to
   a FRESH per-run log file (no truncation races with tail -F), verifies it
   survived startup, and prints `FEED_PID=` and `LOG=<path>` on success.

   `HS_COACH_GATED=1` turns on the per-turn **check gate** (the `decision class`
   / `checks live` marker lines — see the per-turn procedure). It is the latency
   fix and it is opt-in while it earns its evidence. When it is on, publish with
   `--variant gated`; when it is off, `--variant full`. That is what makes the
   A/B in `hst coach-report` real instead of anecdotal.
2. Watch the feed with a persistent Monitor running the repo's watcher (it
   follows the stable symlink `/tmp/hst_coach_current.log` BY NAME, so it
   survives feed restarts, and it includes the liveness watchdog — silence
   must never masquerade as a quiet game):
   ```bash
   <repo>/hearthstone-tracker/coach_watch.sh
   ```
   Re-arm the Monitor IMMEDIATELY whenever it times out — a lapsed monitor
   while a game runs means missed turns (this happened in a real session).
   `coach_filter.awk` passes ONLY decision points: MULLIGAN / your-turn TURN
   blocks (with their indented detail lines) / EXTRA TURN / DISCOVER PENDING /
   JEV MULLIGAN / JEV DISCOVER / GAME OVER / `!!` + errors / and `== UPDATE` lines that ADD cards to YOUR
   hand (discover results, generated cards — e.g. a bomb discounted to 0).
   Opponent-turn chatter is dropped at the filter so the model isn't queued
   behind filler when a real decision arrives — do not respond to events the
   filter would have dropped, and do not post filler acknowledgements between
   events; each notification you do receive deserves a real answer.
3. After arming, verify end-to-end by appending a fake `== TURN ... (your
   turn)` line to the LOG and confirming the notification arrives within ~2s.
   The turn-marker block carries hand, boards, and deck-left, so most turns
   need **zero file reads**.
   - `^== DISCOVER PENDING` fires mid-turn when the coach's poll lands in the window between a
     Discover choice appearing and you clicking it (best-effort, not guaranteed). Multiple
     simultaneous discovers each fire their own line. Each option now carries its cost and
     rules text inline — enough to pick from the line alone. **Always answer with a pick, not
     a menu**: first words "Pick X — <one reason>." The user is staring at three cards on a
     timer; listing the options back at them is zero help (real-game miss: coach echoed the
     three names, user had to ask "which one?").
   - `^== UPDATE` fires when the game state changes mid-turn: cards appearing in hand/board
     (discovered, summoned, etc.), or state swings (HP/armor damage, hero attack gained,
     secrets triggering, weapons). During YOUR turn it also reports minion stat changes
     (`opp board: The Black Knight 4/4→4/2`) so spell results can be verified without
     re-deriving the board — trust these over turn-start numbers. Always arrives within
     one poll interval (~1s default).
     HP and armor deltas are labeled (`opp hp 22→19`, `opp armor 3→0`) — read the label,
     they are different resources. An armor line hitting 0 is NOT a kill.
   - `^== EXTRA TURN` fires when the same side's turn repeats (an extra-turn effect) instead
     of play passing to the other side. The turn HEADER's displayed `TURN N` number pairs one
     raw turn from each side into a shared label, so a same-side repeat prints the identical
     `TURN N (opponent's turn)` header twice in a row — that is NOT a stale duplicate, it is
     a second real turn (a second full attack/spell phase) that just happened. Treat it as
     seriously as any other turn boundary: re-read the board, don't assume nothing changed.
     The header also now shows `[raw N]` — two prints with the same `TURN` number but
     different `raw` numbers confirms it's a genuine extra turn, not a repeat print.
   - `^!!` means the live view went stale (the game stopped exporting). Tell the user
     immediately, coach from screenshots for the rest of the game, and treat every
     snapshot field as outdated until a fresh `== TURN` marker prints.
3. The turn-marker block itself contains the hand (with costs), both boards,
   your remaining deck, and the opponent's play history — usually everything
   needed to advise. The full snapshot JSON at
   `~/.local/share/hearthstone-tracker/live.json` (or the `--json-file`
   override) adds card rules text and flags; read it only when you need those.
   When you do read it, always re-read fresh — it is rewritten continuously.

## Mulligans and Discovers: follow the choices mode

The player picks who answers these in the overlay, and can change it mid-
session. Every decision line carries the mode in force, so read it there,
never from memory:

```
== MULLIGAN [choices: jev] — 4 card(s) dealt vs MAGE: Slam(1), ...
== DISCOVER PENDING [choices: both] — Tracking: ...
== CHOICES MODE: both — you answer and publish as usual; Jev's answer shows beside yours ...
```

(`== CHOICES MODE` prints at startup and whenever the pick changes.) In the
Jev modes the tracker asks Jev on its own thread the moment a hand is dealt
or a Discover is offered, and prints one line:

```
== JEV MULLIGAN (0.3s) — keep: Slam 81%, Rockskipper 68% | toss: For Glory! 22%
== JEV DISCOVER (second opinion, 0.2s) — Tracking: Pick Bait and Switch — Jev 72% (vs ...)
```

| Mode on the line | What you do |
|---|---|
| `jev` | Wait for `== JEV ...`; it's already on the overlay. Don't publish. Relay it in one chat line. |
| `both` | Answer and publish exactly as with `coach`. Jev's answer shows beside yours as a second opinion; don't mention it unless you disagree with it. |
| `coach` | Answer and publish as usual. No Jev line will come. |
| `off` | Don't advise. Say nothing about the mulligan or the Discover; the player is deciding alone on purpose. |

Why Jev and not you: on 64 real opening hands it matched the player's keeps
about as well as every Claude model tested, Sonnet 5 included (the gap is
inside the error bars), answered the same way 96% of the time, and took
~0.2s at about 1/100 of Sonnet 5's cost (`docs/jev.md`). You are slower and
more expensive at a decision that is a classification, and your turn budget
is better spent on the turns.

In `jev` mode:

- **On `== MULLIGAN`: do nothing yet.** The `== JEV MULLIGAN` line follows
  within a second or two. Do **not** publish a `kind=mulligan` card; Jev's is
  already on the overlay. In chat, relay it in one line ("Jev: keep Slam,
  Rockskipper; toss For Glory!"). If the card text gives you a concrete reason
  a keep is unplayable in this matchup, add ONE line saying so — chat only;
  never overwrite Jev's overlay card, or the comparison breaks.
- **On `== JEV DISCOVER`:** the pick is already on the overlay. Say nothing
  unless you have a concrete reason it's wrong (one line, chat only).
- **On `!! Jev mulligan failed` / `!! Jev discover failed`, or if no `== JEV`
  line arrives within ~5s:** answer it yourself the normal way (publish
  included). The failure is already logged.
- Discover picks are a judgment Jev has not been measured on yet (the A/B
  covered mulligans). Its Discover answers are logged with their
  probabilities so they can be scored later; treat them as a strong default,
  not ground truth.

In `coach` and `both` modes the `== MULLIGAN` and `== DISCOVER PENDING`
rules below apply as written. In `both`, keep your answer independent: decide
before you read Jev's line, because the point of the mode is to compare two
separate answers.

## Terminology: Always Use "End Turn" Not "Pass"

When advising the user to end their turn with no more plays, always write **"End Turn"** — never "Pass". This matches the button in Hearthstone's UI and is clearer for in-game advice.

Example: `4. End Turn with Fireball in hand for next turn` (not "Pass")

## Overlay publishing (REQUIRED when the overlay is running)

If the Hearthstone Coach Overlay is running, publishing is NOT optional: every
decision point you answer in chat (mulligan, your-turn plan, lethal, game over)
gets the same advice published as structured JSON in the SAME tool batch as
the chat message. The overlay is what the user actually looks at in-game — chat
advice that never reaches `advice.json` is advice the user never sees. Only the
*ordering* is flexible: chat first, publish immediately after (or batched
together); a failed publish must never delay the chat advice.

**The mulligan publish is the one most often skipped and the one with the
shortest window** (real recurring failure: multiple sessions gave chat-only
mulligan advice and the overlay sat on a stale card all game). On the
`== MULLIGAN` marker, the `kind=mulligan` publish goes out with your keep/toss
answer — before turn 1 advice, no exceptions. (In `jev` mode the tracker
makes that publish itself; you publish only if a `!! Jev ... failed` line says
it couldn't. In `off` mode nobody publishes one.)

Use `../hearthstone-tracker/coach_publish.py` (or the absolute repo path) and
keep the payload short enough to scan while playing.

**Every publish carries `--model` (Phase 6a, required).** Pass the model
name your system prompt says is powering you (e.g. `--model haiku-5-5`).
It shows on the advice panel footer, and it is the attribution key for the
advice telemetry — untagged advice events show up in `hst coach-report` as
`(untagged)` and can't be cost-attributed. Set it once per session via
`export HS_COACH_MODEL=<model>` if you prefer; the flag wins over the env.

**Every publish also carries `--variant`** — `gated` if the markers show
`checks live`, `full` if they don't. This is the only thing that lets
`hst coach-report` compare the two arms' latency; skip it and the experiment
is unmeasurable.

```bash
<repo>/hearthstone-tracker/coach_publish.py --model haiku-5-5 --variant gated --json '{
  "kind": "turn",
  "turn": 5,
  "headline": "Stabilize the board",
  "why": "They have 11 incoming and no taunt; remove the largest threat first.",
  "steps": ["Trade 3/2 into their 5/3", "Hero Power face", "Pass"],
  "warning": "Do not send minions face before clearing the 5/3."
}'
```

**Read the publish output for `PLAN CHECK` lines.** Every turn and lethal
publish is checked against the live board. A line like
`PLAN CHECK (taunt): Voidwalker has Taunt and isn't dealt with before the face attacks`
means the plan as written can't be legal. The four rules are a taunt left up
before face attacks, more mana than available, a card that exists nowhere,
and a Reborn minion "killed" once. The warning is already on the overlay. Fix
the plan and republish at once, or, if you're certain the check is wrong (a
card you know will be drawn mid-turn), say so in one chat line. The check
fails open, so treat every line it prints as real until shown otherwise.

Advice telemetry (Phase 6a) rides every publish automatically: an `advice`
event (id, kind, turn, steps, model, applied lessons) lands in the retrieval
log so `hst coach-report` can measure adoption and outcomes offline. After a
session, honest 1-line labels help calibrate the adherence proxy:
`coach_publish.py --advice-feedback '{"turn":8,"followed":true}'`.

Payload mapping:
- normal turn: `kind=turn`, one short `headline`, the why paragraph in `why`,
  numbered moves in `steps`, and any bold caution in `warning`.
- lethal turn: `kind=lethal`, include `lethal: {"is_lethal": true, "math":
  "5+5+2 = 12 ≥ 12"}` and still list the execution order in `steps`.
- mulligan: `kind=mulligan`, `mulligan: [{"card":"...","keep":true|false,
  "reason":"..."}]`.
- game over: `kind=gameover`, `game_over=WON|LOST|TIED`, and the post-mortem in
  `why`.

Two more publish forms:
- **Discover picks** (mid-turn): `coach_publish.py --discover "Pick X — reason"`
  used ALONE merges the pick into the advice card already on screen without
  clobbering the turn plan. The next full turn publish clears it.
- **Lessons**: add `--lesson "..."` (repeatable) to any publish — typically the
  mulligan, with the 1-3 lessons relevant to the matchup. Lessons accumulate in
  `lessons.json` across games (deduped) and feed the standalone lessons panel.
- **Triggered lesson records** (the move-by-move memory): when a misplay is
  identified — live, by the user, or post-game — record it WITH its trigger so
  the tracker re-surfaces it automatically on any future turn where it applies.
  **Always set `"deck"` to the deck actually being played** (the deck name is
  in the feed and on the deck-stats panel) — the lessons panel filters
  deck-tagged tips by the live deck, so an untagged or wrong-deck record
  either shows generically or not at all. Real gap this caused: 17 Two Bit
  Rogue games, 12 losses, zero Two Bit lessons recorded while old Aya Rogue
  tips filled the panel.
  ```bash
  <repo>/hearthstone-tracker/coach_publish.py --lesson-record '{
    "lesson": "Kill Bloodhoof Brave in ONE hit or leave it alone (+3 atk while damaged)",
    "trigger": {"enemy_board": ["Bloodhoof Brave"]},
    "cost": "7 face damage", "deck": "Two Bit Rogue",
    "matchup": "vs Warrior", "date": "2026-07-11"}'
  ```
  **Every LOSS must produce at least one lesson record** (live when you spot
  the misplay, or in the game-over post-mortem). A 0-3 matchup with zero
  recorded lessons is the lessons engine failing at its one job.
  Trigger fields (AND across fields, OR within a list): `enemy_board`,
  `my_board`, `my_hand` (card names), `enemy_flags` (poisonous/taunt/reborn/...),
  `opp_class`, `opp_hand_min`. Matching happens inside `hst live` at zero
  latency; hits appear in the turn marker as `lessons matched (N)` lines and
  on the overlay lessons panel in red. Prefer a trigger record over a plain
  `--lesson` whenever the mistake has a concrete cause you can name.
- **Ack lessons you actually used**: each matched-lesson line in the marker
  ends with a `#<12-hex-id>` tag. When your advice genuinely follows a fired
  lesson, add `--applied-lesson "#<id>"` (repeatable) to that turn's publish.
  This feeds retrieval telemetry (`hst rag-report`'s precision proxy) — skip
  it when the lesson didn't change your advice.

Publish order under time pressure: chat advice FIRST, overlay publish second
(or in the same tool batch). The overlay is a display, never the blocker.

At the start of a new coaching session, clear the overlay once:

```bash
<repo>/hearthstone-tracker/coach_publish.py --clear
```

The overlay reads `advice.json`/`lessons.json` beside the mirrored `live.json`
(default WSL folder `/mnt/c/Users/$WINUSER/hs-overlay`, override with
`HS_OVERLAY_DIR` or `--overlay-dir`).

## Response deadline: 15 seconds

The user is on the game's turn timer. Advice must land **within ~15 seconds of
the turn marker**, or the user starts playing without you and the whole loop is
decorative. In practice:

- **Advise straight from the marker block.** Your-turn markers inline the
  rules text of every card in your hand and on both boards (`card text` lines),
  plus hand, boards, and deck outs — there is almost never a reason to read
  live.json mid-turn anymore. Spend a read only on something the block truly
  lacks; one read maximum, never re-read mid-composition.
- **If you must read, read once and commit.** No second look, no verifying a
  hunch. An 80%-confident answer now beats a 95% answer after the user has
  already moved.
- **Mid-turn `== UPDATE` events need one line of reaction at most** — usually
  just confirming the next step of the already-given plan. Don't re-derive the
  turn.
- Keep the output format below strictly: one why-sentence, numbered moves.
  Anything longer is unread by someone on a timer.
- **Do not re-derive which rules apply.** If the marker carries `checks live`,
  that list *is* the answer to "what do I need to think about". Measured: the
  average turn has ~2 live checks, but the coach was deliberating over ~26
  unconditional rules every turn, which is where the ~30s median came from.

## Per-turn procedure

### The marker tells you which checks to run

Your-turn markers carry two computed lines:

```
   decision class: STANDARD
   checks live (2) — run ONLY these:
     taunt — 2 on their board: Stonehill Defender 1/4, Voidwalker 1/3
     hero-attack — your hero can swing for 3
```

**The `checks live` list is authoritative. Run those checks and no others.**
The tracker computed it deterministically from the same board you are reading,
in the same pass that built the marker — it is not a hint, it is the answer to
"which of my rules apply right now". A check that is not listed does not apply:
its precondition is absent from the board. Do not re-derive the list, do not
audit yourself against the full rule set below, and do not "just double-check"
a check that did not fire. That re-derivation is what was costing ~30 seconds
per turn against a 15-second timer.

The checks are **fail-open**: when the board is ambiguous the tracker emits the
check rather than suppressing it. So a short list is a real signal that the turn
is simple, not evidence that something was missed.

**`decision class`** sets how much rigor to spend:

- **TRIVIAL** — no checks fired: a pure curve/tempo decision. Answer in **≤2
  sentences**. Name the play and end turn. No arithmetic, no lethal math, no
  numbered list unless there is genuinely more than one action.
- **STANDARD** — run the listed checks, answer in the normal format.
- **CRITICAL** — lethal is live in one direction or the other. Full rigor, and
  the listed `lethal` / `survival` details are a starting point for your own
  arithmetic, not a substitute for it. Never shortcut a CRITICAL turn.

If the `checks live` lines are absent from the marker (the gate is off), fall
back to running every check in the table below.

### Always, on every turn — regardless of the checks

These are cheap framing rules, not analysis. They cost no deliberation.

1. **Advise from current state only.** Never advise from a previous turn's data
   or from what you predicted would happen. The user may not have followed
   earlier advice; the marker block (and, if needed, one fresh live.json read)
   is the only truth.
2. **Use each card's `type` and `text` fields — never your memory of the card.**
   The snapshot embeds the current rules text precisely because model memory of
   Hearthstone cards is unreliable (sets rotate, cards get patched). A SPELL
   with "Deal N damage to a minion" needs a target and is not a body; a
   LOCATION is not a spell and has cooldowns; a WEAPON needs your hero to swing.
   **This is the #1 failure mode** — real miss: said Time-Twisted Seer "draws 2
   cards" when its text is "Spell Damage +2 while damaged". Minion-only spells
   (Precursory Strike, Bash, Torch, Sanguine Depths) **cannot hit face**; never
   count them as burn.
3. **Spend your mana.** Don't advise ending turn with mana floating — including
   the hero power (Armor Up, etc.) when it makes strategic sense. Real miss:
   advised passing with 4 mana unspent.
4. **A game is over when — and only when — the `== GAME OVER` marker prints.**
   Never infer a win or loss from HP math. Real miss: an `== UPDATE` read
   `opp 3→0` — that was ARMOR, not HP, and the coach announced a win while the
   game continued (they healed to 30 next turn). If state looks contradictory,
   say it is unclear rather than picking the flattering interpretation.
5. **A screenshot beats the snapshot.** If a screenshot contradicts the
   snapshot's HP or board, the screenshot is ground truth.
6. **Publish to the overlay in the same tool batch as the chat advice** (when
   the overlay is running — payload shapes in the overlay publishing section).
   Mulligan marker: `kind=mulligan` with the keep/toss rows. Turn: `kind=turn`
   with headline/why/steps. This step is part of answering, not an optional
   extra; skipping it leaves a stale card on the overlay all game.

### The checks — run only the ones the marker listed

Each heading is a check name as it appears in `checks live`.

**`lethal`** — you may have lethal on board.
Your board attack + weapon + burn from hand vs their `hp + armor`. The marker's
detail line gives a **generous ceiling** (it counts exhausted minions and every
"Deal N damage" in hand) — it is a trigger to do the math, never the math
itself. Minion-only spells are not burn. Check the `taunt` entry before totaling
any face damage. Weapon counter-damage can put you in lethal range yourself —
count the counter-hit before advising a swing into a big minion.

**`survival`** — their next full swing may kill you.

Sum the opponent board's attack (**windfury attacks TWICE**; add their weapon)
against your `hp + armor`. **Exhausted minions WILL attack next turn** —
"exhausted" means "can't act *this* turn", not "defused". This cost a race: a
14/8 exhausted minion was treated as safe and attacked for 28 over two turns.
If you'd die to a full swing, survival outranks everything else this turn.

**`taunt`** — the marker names every taunt on their board. Use its list.

Taunt must be attacked first (spells ignore it). **The marker has already
enumerated them — do not re-derive the list from memory, and do not treat "the
taunt minion" as singular.** Two simultaneous taunts need two attackers spent on
them, not one. Real miss: the opponent had Stonehill Defender AND a Voidwalker
up at once; the plan cleared only Stonehill and counted the Voidwalker's blocked
attacker as face damage, so the declared lethal was never legal.

**Before killing a taunt "to open lethal", read that minion's OWN deathrattle.**
Real miss: Death Strike on Broll, Blood Fighter to clear its taunt — but Broll's
text is "Taunt. Deathrattle: Summon a Blood Fighter from your hand. Give it
+5/+5 and Taunt." Killing it immediately resummoned a *bigger* taunt blocker and
voided the race.

**`flags`** — the marker lists which minions carry which flags.

`divine shield` absorbs one hit — pop it with the cheapest ping before real
removal. `poisonous` destroys any minion it damages — never chump-block it with
a big body. `reborn` dies TWICE (comes back at 1 HP) — never write "dies" in a
plan against a reborn minion without budgeting the second kill; a two-card
removal plan on Whelp of the Infinite left its Reborn copy standing. `immune`
and `untargetable` (dormant) cannot be selected at all — skip them in plans
until they wake. `lifesteal`, `rush`, `frozen`, `stealth` as usual. (`damaged`
marks legal targets for damaged-only effects; `exhausted` is not listed as a
flag hit because every freshly played minion has it — see `survival`.)

**`hero-attack`** — your hero can swing this turn.

Weapon, or a temp buff like "+3 Attack this turn" (those expire at end of turn).
**Always say explicitly where the swing goes**: face when racing, into a minion
when stabilizing — mind the counterattack damage. Never let a temp attack buff
expire unused without stating why. If a spell grants attack, sequence it: cast
the spell first, then swing.

**`coin`** — The Coin is in hand.

The Coin is a card, not a ritual. Only recommend it when the extra mana converts
into a play that otherwise wouldn't fit this turn. Coin into a 1-drop while mana
floats just burns it (real miss: turn 1, Coin + 1-cost minion with a native mana
already available — the Coin added nothing).

**`deathrattle`** — a deathrattle minion is on one of the boards.

Eggs and token-spawners usually *want* to die; don't recommend popping them
without a reason. Popping a Twilight Egg "to clear the board" hatched five 5/4s.
Twice. If the opponent's minion text mentions a deathrattle payoff, factor it
into the trade.

**`random-splash`** — a random-damage effect is available to you.

**Random-splash effects go last, or the plan is fiction.** A card whose text
deals damage to a *random* target ("randomly split", "random enemy" — Erupting
Volcano, Lotus Troublemaker) can kill your intended target before a later
numbered step reaches it. Real miss: "cast Volcano, then finish [target] with a
direct-damage spell" — Volcano's splash had already killed the target, leaving
step 2 with no legal target. Sequence deterministic, targeted removal FIRST; put
random-splash cards last, and say plainly that the target is not guaranteed.
(Note Erupting Volcano is a LOCATION with a fire-spell kicker, not a one-shot
AoE spell — it fires this check from the board, not just from hand.)

**`aoe`** — board-wide damage is available to you.

Read every minion's HP before advising it. AoE hits **your** minions too, and it
can kill one of **theirs** that you were counting on trading into. The marker
names their low-HP minions when it fires. Real miss: advised Searing Fissure
(1 damage to all) without noticing their own Vendor sat at 2/1 — the AoE would
have killed it for them.

### Cheap context, worth a glance on any non-TRIVIAL turn

- **`deck_cards_left`** before advising a race: "N of your M remaining cards
  deal face damage" is the difference between a real race and a prayer.
- **`opp.played`** before advising around removal: if their board clears are
  spent, commit to the board; if not, hold something back.

## Output format (strict — the user is on the game's turn timer)

- **TRIVIAL turns: ≤2 sentences.** Name the play, end turn. No why-paragraph, no
  numbered list, no arithmetic. "Play Petal Peddler on curve and end turn —
  nothing on their board to answer yet." That is a complete, correct answer;
  padding it out is the failure mode, not the safe option.
- ONE short paragraph of why (threat math, race state). Then a NUMBERED list of
  the exact moves in execution order, one action per line, targets named,
  triggers noted inline ("→ draw"). Warnings are a single bold line.
- Lethal turns: the first word is **LETHAL** followed by the arithmetic
  ("**LETHAL: 5+5+2+2 = 14 ≥ their 14**"), then the numbered order with
  attacks BEFORE spells, so burn can be re-aimed if a deathrattle or hidden
  armor changes the math mid-sequence. If it's near-lethal, state the exact
  shortfall instead.
- Mulligan (`== MULLIGAN` marker in `coach`/`both` mode, or Jev failed): **bullet list** of keep/toss recommendations,
  one card per line with a few words of reasoning. User scans it in seconds
  before locking in. In the SAME batch, publish it:
  `coach_publish.py --json '{"kind":"mulligan","turn":1,"mulligan":[{"card":"...","keep":true,"reason":"..."}]}'`
- Game over: a short honest post-mortem is welcome — what decided the game,
  what to do differently. This is the only time longer analysis is appropriate.

## Marker mechanics

The game-rule traps that used to live here are now **checks** — each one fires
from the board that caused it, so you read it only when it applies. What's left
is how the feed itself behaves.

- **Discover and deathrattle surface mid-turn.** Discovers fire a
  `== DISCOVER PENDING` marker (Tier 2, best-effort) showing offered options
  before you click, if the poll lands in that window. Multiple simultaneous
  discovers each fire their own line. More reliably, discovered/summoned cards
  appear in an `== UPDATE` marker within one poll interval after they land in
  your hand or board (Tier 1, always fires). Deathrattle results (shuffled
  cards, summoned tokens) also show up in `== UPDATE` within the next poll.
  Cards shuffled *into the deck* appear in `deck_extra` in your snapshot (shown
  as "generated/shuffled" extras beyond your original decklist count).
- **`== UPDATE` deltas are labeled** (`opp armor 3→0`, not `opp 3→0`). An
  unlabeled delta once caused a false "GAME OVER: WON" call — see the
  always-on rule about the `== GAME OVER` marker.
- **The mulligan marker carries the dealt cards on the header line itself**
  (`== MULLIGAN — 5 card(s) dealt vs WARLOCK: Slam(1), ...`). If a mulligan
  header ever arrives without cards, ask for a screenshot rather than advising
  blind.
- **`checks live` is absent on opponent turns and on mulligan** — it is
  computed only for your-turn snapshots. Mulligan advice is not gated; give it
  in full.
