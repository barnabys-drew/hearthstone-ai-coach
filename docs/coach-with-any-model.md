# Running the live coach with any AI model

Turn coaching is **model-agnostic by design**: no part of it imports a
provider SDK or expects a specific model. The one model API the stack calls
itself is TypeSafe's Jev, for mulligans and Discovers. It's opt-in
(`HS_JEV=1`), runs inside the tracker, and is covered in [jev.md](jev.md).
The pipeline is plain CLIs and files:

```
Hearthstone Power.log
      │  (game's own logging — no memory reading)
      ▼
hst live  ──►  live.json + turn markers ──►  coach_filter.awk
      │   │                                         │
      │   └─► Jev (HS_JEV=1, background thread)     │
      │       mulligan + Discover answers           │
      │       ─► overlay + "== JEV ..." feed line   │
      ▼                                             ▼
overlay panels (Electron / browser)          YOUR AI AGENT (turn plans)
      ▲                                             │
      └────────── coach_publish.py  ◄───────────────┘
                  (advice.json / lessons.json)
```

The "coach" is any agent that can do three things:

1. **Run shell commands** — start `coach_feed.sh`, publish with
   `coach_publish.py`.
2. **React to new lines from a long-running process** — watch
   `coach_watch.sh` output (a background-process monitor, or just re-reading
   the current feed log between turns).
3. **Follow markdown instructions** — the playbook in
   [`hearthstone-live-coach/SKILL.md`](../hearthstone-live-coach/SKILL.md)
   contains the whole coaching contract: setup, per-turn checklist, output
   format, and known traps. It is deliberately written so coaching quality
   comes from the *procedure*, not from any one model's Hearthstone knowledge
   (models are explicitly told to trust the feed's card text over memory).

## Per-agent setup

**Claude Code** — skills are native. Symlink or copy the skill folders into
`~/.claude/skills/` and say "run the hearthstone live coach":

```bash
ln -s "$(pwd)/hearthstone-live-coach" ~/.claude/skills/
ln -s "$(pwd)/hearthstone-overlay" ~/.claude/skills/
```

**OpenAI Codex CLI** — point it at the playbook as project instructions
(`AGENTS.md`) or paste the SKILL.md at session start:

```bash
cat hearthstone-live-coach/SKILL.md >> AGENTS.md   # or reference its path
codex "follow hearthstone-live-coach/SKILL.md and coach my game"
```

**Cursor / Windsurf / other IDE agents** — add
`hearthstone-live-coach/SKILL.md` to the agent's rules/context (Cursor:
`.cursor/rules/`), then ask it to start coaching.

**Gemini CLI** — reference the playbook in `GEMINI.md` or pass it as context:

```bash
gemini "read hearthstone-live-coach/SKILL.md, then coach my live game"
```

**Any chat model, no agent harness at all** — manual loop: run
`./hearthstone-tracker/coach_watch.sh` in a terminal, paste each `== TURN`
block into the chat with the SKILL.md as the system prompt, and read the
model's numbered plan. Slower, but the contract is identical.

## What stays the same regardless of model

- The feed only ever contains information your client is allowed to see
  (opponent hands appear as hidden-card counts).
- Turn markers inline every card's rules text, so the model never needs to
  rely on (frequently wrong) memorized card knowledge.
- `coach_publish.py` accepts plain CLI flags or a JSON payload, so any agent
  that can run a command can drive the overlay panels.
- Lessons accumulate in `lessons.json` / `lessons.md` as plain text — a new
  model inherits everything previous models learned about your play.

## Practical notes on model choice

Turn advice is latency-sensitive (~15 seconds before the player moves on).
Prefer a fast model for live coaching; use a stronger/slower one for
post-game analysis (`hearthstone-post-game-coach`), where there is no timer.

What the mulligan comparison in [jev.md](jev.md) says about the coaching
models themselves (64 real hands; agreement with the player's keeps, not
correctness):

- **Sonnet 5 was the strongest Claude arm.** It agreed with the player's
  keeps measurably more often than Haiku 4.5 or Haiku 5.5.
- **Haiku 5.5 at `medium` effort wasn't measurably better than Haiku 4.5**
  on this task. It costs about 3.5x less per hand.
- **Haiku 5.5 at `low` was measurably worse than at `medium`.** Turning the
  effort down for speed is a quality trade.
- **Choice decisions are better off on Jev:** it ties all of them within the
  error bars, at 0.2s and about 1/100 of Sonnet 5's cost.
