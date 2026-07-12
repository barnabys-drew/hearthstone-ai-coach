---
name: hearthstone-post-game-coach
description: Analyze a completed Hearthstone game from the logs. Identify the losing decision, suggest deck tweaks, and coach on game mechanics for the next game. Use this after a game ends — triggered by "== GAME OVER" from hearthstone-live-coach, or standalone off "./hst watch"'s own completion line if you're not running live-coach.
---

# Hearthstone Post-Game Coach

After a game ends, read the full game log and tracker database to give structured coaching feedback: **why you lost**, **what to change in the deck**, and **what game mechanics to focus on next game**.

Unlike the live-coach (real-time turn-by-turn), this skill analyzes the complete game state ex post facto. You get deeper insight without time pressure.

**This skill does not require `hst live` or `hearthstone-live-coach`.** It only
needs the tracker database and Power.log, both of which `./hst watch` already
provides. If you don't want turn-by-turn advice, run `hst watch` instead of
`hst live` and trigger this skill off watch's own completion line — see
[Invoking the Skill](#invoking-the-skill) below.

## When to Use

**With live-coach running:** when it prints `== GAME OVER: LOST` (or `WON`,
for "what went right"):

1. Ask the post-game coach: "Analyze game [timestamp] — why did I lose? What should I change?"
2. The skill reads the game record from the tracker database and Power.log
3. You get a structured report: deciding turn, deck analysis, mechanics feedback

**Post-game-only (no turn-by-turn advice):** run `./hst watch` in the
background instead of `hst live`, and watch its stdout for the completion
line it already prints per game:
```
[2026-07-05 10:24:22] GT_RANKED Burn Warrior vs HUNTER (opponent#1234): WON in 11 turns
```
On that line, invoke this skill directly — same analysis, no live-coach or
`hst live` dependency, no per-turn interruptions during the game.

## What the Skill Does

### 1. Identify the Deciding Turn
Not the turn you died — the turn where the game became unwinnable or where a different play would have changed the outcome.

**Example:** You died turn 9, but turn 7 was deciding: you played around the wrong threat, left your board open, or overextended into a board clear you couldn't answer.

### 2. Analyze the Play Sequence
- What were your hand, board, and mana each turn?
- What did your opponent play?
- Where did your play diverge from optimal?
- Which turn was the last point where you could have won?

### 3. Deck Analysis
Compare your deck list to the game:
- Did you have the right cards in hand when you needed them? (mulligan kept cards that rotted)
- Were you missing a key card type? (no stabilization, no draw, no burst at the end)
- Would swapping one card have changed the outcome? (e.g., "Erupting Volcano is too slow here; Searing Fissure would have cleared turn 5")

### 4. Mechanics Feedback
Teach one specific lesson from this game:
- **Threat assessment:** "You cleared their 3/2 but left the 5/4 (taunt) alive. The taunt was the actual threat."
- **Tempo vs. value:** "Turn 4 you drew a card with Prize Vendor; turn 5 they untapped with three cards. The tempo swing was lethal."
- **Sequencing:** "Your removal order mattered. If you'd Tornado-cleared first, then played Vendors, you'd have traded better."
- **Mulligan:** "You kept Shield Block against Burn Mage; against Mage you want early removal. Next game, mulligan it away."
- **Mana efficiency:** "You played 1 card per turn for 3 turns. Meanwhile they dumped their hand. You were outpaced."

## Input Data

The skill accesses:
- **Tracker database** (`~/.local/share/hearthstone-tracker/games.db`): game metadata (start_time, game_type, format, deck_name, friendly_hero, opponent_class, result, turns)
- **Game cards** table: per-card events (drawn, played, mulligan kept/offered, mull_offered, mull_kept, first_played_turn)
- **Power.log** from the session: full packet stream for reconstructing board state, hand state, and play sequence

## Output Format

### Header
```
== POST-GAME: [Deck Name] vs [Opponent Class] — LOST in [N] turns
```

### Section 1: The Deciding Turn
```
Deciding Turn: [Turn #]
—
[1-2 sentences on what went wrong]

Board state:
  You: [minions], [HP/armor], [mana spent/available]
  Opp: [minions], [HP], [just played: card list]

The play:
  You played: [card list in sequence]
  Better play: [what you should have done]

Why it mattered: [why this turn was the inflection point]
```

### Section 2: Deck Feedback
```
Card Analysis:
—
Rotted in hand: [cards you drew but never played, or played too late]
Missing types: [removal/draw/board/burn you didn't have when needed]
One swap: [e.g., "-1 Erupting Volcano +1 Searing Fissure — would've cleared their wide board turn 5"]
```

### Section 3: Mechanics Lesson
```
Next Game Focus:
—
[Category]: [One specific mechanic to practice]

Example from this game:
[Concrete instance from the logs showing why it matters]

Fix: [How to play it better next time]
```

### Section 4 (if WON): What Went Right
```
Winning Turn: [Turn #]
—
[What you did that locked the win]
Deck strengths: [Which cards carried the game]
Mechanics to repeat: [What felt clean that you did again next game]
```

## Examples

### Example 1: Control Mage loses to Burn Warrior

```
== POST-GAME: Control Mage vs WARRIOR — LOST in 11 turns

Deciding Turn: 7
—
You had 22 HP. They had a 3-mana Erupting Volcano in hand and a clear board.
You played Ice Block + Frost Nova (defensive, 5 mana). They untapped, played
Volcano (3 mana), and Shield Block (2 mana). Next turn, they had 6-mana for
their hand dump. You were out-temped.

Board state:
  You: Apprentice (1/1), 22 HP, 2 armor
  Opp: empty, 18 HP, just played: Slam, Bash, Rockskipper (3 cards in one turn)

Better play: Turn 6, not turn 7. You had Flamestrike in hand. Play it a turn
early (a 7-mana key card) so they can't untap and explode. Or mulligan Frost
Nova away next time — it's worse than Flame Geyser vs. early board.

Why it mattered: Turn 7 was when they pivoted from board control to burn velocity.
If you'd cleared THEIR hand (Flamestrike turn 6), they couldn't dump the rest on
turn 7.

Card Analysis:
—
Rotted in hand: Frost Nova. You played it turn 7 as defense; it would've been
better turn 5 to prevent the Rockskipper hit.
Missing types: Early board clear. You had Flamestrike (turn 7+) but no Searing
Fissure or early AoE to answer turn 5 Rockskipper.
One swap: -1 Frost Nova +1 Searing Fissure. Warrior boards turn 2-5 would've
been cleared cleanly.

Next Game Focus:
—
Threat Assessment: You treated the empty board as "safe" because they had no
minions. But Burn Warrior's threat is tempo (hand refill + cheap spells), not
board. Empty board is MORE dangerous — they're setting up a combo turn.

Example from this game:
Turn 6: Their board was empty, 3 cards in hand. You felt safe and played Ice
Block. They untapped with Slam, Bash, Rockskipper (3 new cards drawn), and
tempo'd you out. The emptiness was the signal, not a relief.

Fix: Against Burn Warrior, when they clear their own board and still have cards,
assume they're one turn away from dumping the hand. Play proactively (Flamestrike
early, Force of Nature for tempo) instead of reactively (Ice Block after they
attack).
```

### Example 2: Burn Warrior wins vs. Control Hunter

```
== POST-GAME: Burn Warrior vs HUNTER — WON in 9 turns

Winning Turn: 8
—
You were at 12 HP on your turn. They had an Ebyssian (14/8 exhausted) and
Illusions on board. You Shield Blocked (5 armor), equipped Axe (weapon), and
Eternal Toil'd Ebyssian (draw + 1 damage). Next turn they attacked and hit 22
damage, but you were ready: you'd drawn two Torches the turn before (that's why
you mulligan'd for draw, not board).

Board state:
  You: Axe (2/1), 17 effective HP (12+5 armor), Torches in hand
  Opp: Ebyssian + Illusions (board full)

The play: You focused turn 8 on survival (Shield Block), not removal (no Volcano
play). You read that their board would swing for lethal next turn AND that draw
was your only out. You got it.

Card Analysis:
—
Strengths: Eternal Toil. It drew you into Torches AND damaged Ebyssian. Turn 8
it was the only right play (not Volcano, which would have wasted mana on board
you couldn't answer).
Hand tracking: Turn 7 you knew you were one draw away from Torch. The draw
happened. You'd kept Rockskipper mulligan because it generates Rock (1-mana
damage) — turns out you didn't need it, but it was the right mulligan against
unknown Hunters.

Next Game Focus:
—
Mechanics to repeat: Counting outs. You knew you had 2 Torches in the remaining
7-card deck. Turn 8, Shield Block + draw from Toil was the only line that gave
you a chance. You called it right.

Threat assessment: You saw that Ebyssian was exhausted and STILL played for
survival. Perfect — you didn't fall for the "exhausted = safe" trap.
```

## The Checklist: What to Analyze

When the skill digs into a game, verify:

1. **Turns 1–3:** Mulligan correct? Kept the right cards for the matchup?
2. **Turns 4–6:** Did you establish board or get run over? Was removal sequenced right?
3. **Turns 7–9:** Did you pivot correctly (stabilize vs. race)? Did you play around the right threats?
4. **The deciding turn:** What was the last turn where a different play would have changed the outcome?
5. **Deck list:** Were you missing card types you needed? Would one swap have fixed it?
6. **Mechanics:** What one lesson should you carry to the next game?

## Limitations

- **Incomplete data on opponent hand.** You see what they played, not what they kept. Post-game analysis can infer ("they had a lot of mana left, so maybe burn"), but can't be certain.
- **No counterfactual simulation.** The skill can't replay the game with your proposed "better play" to verify it would have won. It can suggest, but can't prove.
- **Deck meta context is shallow.** The skill sees YOUR deck and THIS game, not the broader meta. If your 60-card pile is off-meta, the skill might not catch it (that's a different analysis).

## Invoking the Skill

```bash
# After a game ends:
./hst stats recent --limit 1  # Get the last game's timestamp
# Then ask the skill:
# "Analyze my last Burn Warrior game (timestamp: 2026-07-05 10:02:48). 
#  Why did I lose? What should I change?"
```

The skill will query the database, read the logs, and produce the structured report above.

### Post-game-only setup (no `hst live`, no turn-by-turn advice)

```bash
# Run the lightweight recorder instead of hst live:
cd hearthstone-tracker && ./hst watch
```
Watch its stdout for the completion line it prints per finished game
(`... WON in N turns` / `... LOST in N turns`). On that line, ask the skill
the same way as above — it doesn't care whether `hst live` ever ran.

## See Also

- **[hearthstone-live-coach](../hearthstone-live-coach/)** — real-time turn-by-turn advice while playing
- **[hearthstone-tracker](../hearthstone-tracker/)** — game history and stats database


## Feed the trigger-matched lesson store

After identifying the losing decision or a repeated misplay, ALSO record it as
a structured, triggered lesson so the live coach re-surfaces it automatically
on future turns where it applies (see hearthstone-live-coach's "Triggered
lesson records" section for the schema):

```bash
<repo>/hearthstone-tracker/coach_publish.py --lesson-record '{"lesson": "...",
  "trigger": {"enemy_board": ["<card>"]}, "cost": "...", "date": "YYYY-MM-DD"}'
```

Pick the most concrete trigger available (a card name beats a class; a class
beats nothing). A lesson without a nameable trigger belongs in lessons.md
prose instead.

Record the lesson promptly — within ~30 minutes of the game ending. New
lessons emit an `ingest` event into the retrieval telemetry log, joined to
the just-finished game by timestamp; `hst rag-report` uses that join to find
games where a misplay happened but retrieval fired nothing (the miss backlog
that gates the later RAG phases).

Additionally, after each session (or when patterns shift), refresh the ONE
synthesized cross-game headline shown at the top of the overlay lessons
panel: a record with `"headline": true` summarizing what wins and loses
games across ALL recorded history — not one game. Newest headline wins.
