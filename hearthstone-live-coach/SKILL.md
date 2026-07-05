---
name: hearthstone-live-coach
description: Coach the user through a live Hearthstone game in real time by tailing the game's own logs. Use this when the user asks for live/in-game Hearthstone advice, turn-by-turn coaching, mulligan help while playing, "what's my play", or lethal checks during a match. Requires the hearthstone-tracker CLI from this repository and a running Hearthstone client with Power logging enabled.
---

# Hearthstone Live Coach

Give play-by-play advice during a real Hearthstone game. The `hst live` command
(in `../hearthstone-tracker/`) tails the game's Power.log and publishes the full
visible game state each turn; you read that state and advise. Everything here is
legal-information coaching: the log only contains what the player's client is
allowed to see (the opponent's hand appears only as a hidden-card count).

This skill is deliberately procedural. Follow it exactly even if you believe you
know the game well — several of the rules below exist because a capable model
got them wrong in live play.

## Setup (once per session)

1. Start the state feed in the background:
   ```bash
   cd <repo>/hearthstone-tracker && ./hst live
   ```
2. Watch its stdout for these markers (e.g. with a background Monitor/tail):
   ```
   ^== MULLIGAN|^== TURN.*your turn|^== DISCOVER PENDING|^== UPDATE|^== GAME OVER|Traceback|Error
   ```
   Do NOT trigger on opponent turns — stay quiet during them unless the user asks.
   - `^== DISCOVER PENDING` fires mid-turn when the coach's poll lands in the window between a
     Discover choice appearing and you clicking it (best-effort, not guaranteed). Multiple
     simultaneous discovers each fire their own line.
   - `^== UPDATE` fires when the game state changes mid-turn: cards appearing in hand/board
     (discovered, summoned, etc.), or state swings (HP/armor damage, secrets triggering, weapons).
     Always arrives within one poll interval (~3s default).
3. On each marker, read the full snapshot JSON:
   `~/.local/share/hearthstone-tracker/live.json` (or the `--json-file` override).
   Always re-read it fresh; it is rewritten continuously and the stdout line is
   just the trigger.

## Per-turn procedure

Work through this checklist before writing anything:

1. **Read the snapshot fresh.** Never advise from a previous turn's data or from
   what you predicted would happen. The user may not have followed earlier
   advice; the snapshot is the only truth.
2. **Use each card's `type` and `text` fields — never your memory of the card.**
   The snapshot embeds the current rules text precisely because model memory of
   Hearthstone cards is unreliable (sets rotate, cards get patched). A SPELL
   with "Deal N damage to a minion" needs a target and is not a body; a
   LOCATION is not a spell and has cooldowns; a WEAPON needs your hero to swing.
3. **Count damage both ways.**
   - Incoming: sum the opponent board's attack (windfury attacks TWICE; add
     their weapon if they have one). Compare against `hp + armor`. If you'd die
     to a full face swing, survival outranks everything else this turn.
   - Outgoing: your board attack + weapon + burn from hand. Check lethal every
     turn from about turn 5 on.
4. **Respect the flags.** `taunt` must be attacked first (spells ignore it);
   `divine shield` absorbs one hit — pop it with the cheapest ping before real
   removal; `damaged` marks legal targets for damaged-only effects;
   `exhausted` minions can't attack this turn (newly played or already acted);
   `frozen`/`stealth` as usual.
5. **Check `deck_cards_left`** (your remaining deck) before advising a race:
   "N of your M remaining cards deal face damage" is the difference between a
   real race and a prayer.
6. **Check `opp.played`** (everything the opponent has cast) before advising
   around removal: if their board clears are spent, commit to the board; if
   not, hold something back.
7. **Deathrattle caution.** Eggs and token-spawners usually want to die; don't
   recommend popping them without a reason. If the opponent's minion text
   mentions a deathrattle payoff, factor it into trades.

## Output format (strict — the user is on the game's turn timer)

- ONE short paragraph of why (threat math, race state). Then a NUMBERED list of
  the exact moves in execution order, one action per line, targets named,
  triggers noted inline ("→ draw"). Warnings are a single bold line.
- Lethal turns: the first word is **LETHAL** followed by the arithmetic
  ("**LETHAL: 5+5+2+2 = 14 ≥ their 14**"), then the numbered order with
  attacks BEFORE spells, so burn can be re-aimed if a deathrattle or hidden
  armor changes the math mid-sequence. If it's near-lethal, state the exact
  shortfall instead.
- Mulligan (`== MULLIGAN` marker): **bullet list** of keep/toss recommendations,
  one card per line with a few words of reasoning. User scans it in seconds
  before locking in.
- Game over: a short honest post-mortem is welcome — what decided the game,
  what to do differently. This is the only time longer analysis is appropriate.

## Known traps (each of these happened in real coached games)

- **Exhausted minions WILL attack next turn.** "Exhausted" means "can't act this turn",
  not "locked down forever". On turn N, an exhausted minion resets and attacks freely
  on turn N+1 (and can even attack multiple times if special mechanics apply). When
  counting incoming damage next turn, treat exhausted minions as full threats, not
  defused ones. This cost a race: I assumed a 14/8 exhausted minion was safe, but it
  attacked twice for 28 damage.
- **Discover and deathrattle mechanics now surface mid-turn.** Discovers fire a
  `== DISCOVER PENDING` marker (Tier 2, best-effort) showing offered options before you
  click, if the poll lands in that window. Multiple simultaneous discovers each fire their
  own line. More reliably, discovered/summoned cards appear in an `== UPDATE` marker within
  one poll interval after they land in your hand or board (Tier 1, always fires). Deathrattle
  results (shuffled cards, summoned tokens) also show up in `== UPDATE` within the next poll.
  Cards shuffled *into the deck* now appear in `deck_extra` in your snapshot (shown as
  "generated/shuffled" extras beyond your original decklist count).
- Advising "play Slam" as if it were a minion — it is a targeted spell and the
  game rejects it with no board. Type/text fields exist so this never recurs.
- Treating Erupting Volcano as a one-shot AoE spell — it is a LOCATION with a
  fire-spell kicker.
- Forgetting windfury doubles attack when counting incoming damage.
- Recommending a weapon swing into a big minion when the counter-damage put the
  player into exactly-lethal range. Count the counter-hit.
- Popping a Twilight Egg "to clear the board" — it hatched into five 5/4s. Twice.
