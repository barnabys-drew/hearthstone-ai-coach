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
   ^== MULLIGAN|^== TURN.*your turn|^== DISCOVER PENDING|^== UPDATE|^== GAME OVER|^!!|^   |Traceback|Error
   ```
   The `^   ` alternation matters: the indented detail lines under each turn
   marker (hand, boards, deck-left) ride along in the same notification, so
   most turns you can advise **directly from the marker block with zero file
   reads**. Do NOT trigger on opponent turns — stay quiet during them unless
   the user asks.
   - `^== DISCOVER PENDING` fires mid-turn when the coach's poll lands in the window between a
     Discover choice appearing and you clicking it (best-effort, not guaranteed). Multiple
     simultaneous discovers each fire their own line.
   - `^== UPDATE` fires when the game state changes mid-turn: cards appearing in hand/board
     (discovered, summoned, etc.), or state swings (HP/armor damage, hero attack gained,
     secrets triggering, weapons). Always arrives within one poll interval (~1s default).
   - `^!!` means the live view went stale (the game stopped exporting). Tell the user
     immediately, coach from screenshots for the rest of the game, and treat every
     snapshot field as outdated until a fresh `== TURN` marker prints.
3. The turn-marker block itself contains the hand (with costs), both boards,
   your remaining deck, and the opponent's play history — usually everything
   needed to advise. The full snapshot JSON at
   `~/.local/share/hearthstone-tracker/live.json` (or the `--json-file`
   override) adds card rules text and flags; read it only when you need those.
   When you do read it, always re-read fresh — it is rewritten continuously.

## Response deadline: 15 seconds

The user is on the game's turn timer. Advice must land **within ~15 seconds of
the turn marker**, or the user starts playing without you and the whole loop is
decorative. In practice:

- **Advise straight from the marker block.** It carries hand, boards, and deck
  outs. Reading live.json costs a tool round-trip — spend it only when the
  block is missing something you actually need (rules text of an unfamiliar
  card, flags, exact mana). One read maximum; never re-read mid-composition.
- **If you must read, read once and commit.** No second look, no verifying a
  hunch. An 80%-confident answer now beats a 95% answer after the user has
  already moved.
- **Mid-turn `== UPDATE` events need one line of reaction at most** — usually
  just confirming the next step of the already-given plan. Don't re-derive the
  turn.
- Keep the output format below strictly: one why-sentence, numbered moves.
  Anything longer is unread by someone on a timer.

## Per-turn procedure

Work through this checklist before writing anything:

1. **Advise from current state only.** Never advise from a previous turn's data
   or from what you predicted would happen. The user may not have followed
   earlier advice; the marker block (and, if needed, one fresh live.json read)
   is the only truth.
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
5. **Spend the hero's attack.** `me.attack` > 0 means the hero can swing this
   turn (weapon, or a temp buff like "+3 Attack this turn" — those expire at
   end of turn). Always say explicitly where the swing goes: face when racing,
   into a minion when stabilizing (mind the counterattack damage). Never let a
   temp attack buff expire unused without stating why. If a spell grants
   attack, sequence it: cast the spell first, then swing.
6. **The Coin is a card, not a ritual.** Only recommend Coin when the extra
   mana converts into a play that otherwise wouldn't fit this turn. Coin into
   a 1-drop while mana floats just burns the Coin (real-game mistake: turn 1,
   Coin + 1-cost minion with a native mana already available — the Coin added
   nothing).
7. **Check `deck_cards_left`** (your remaining deck) before advising a race:
   "N of your M remaining cards deal face damage" is the difference between a
   real race and a prayer.
8. **Check `opp.played`** (everything the opponent has cast) before advising
   around removal: if their board clears are spent, commit to the board; if
   not, hold something back.
9. **Deathrattle caution.** Eggs and token-spawners usually want to die; don't
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
