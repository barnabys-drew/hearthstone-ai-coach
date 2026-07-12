# Installing this skill in AI CLIs

This skill is intentionally portable: the canonical instructions are in `SKILL.md`, and the deterministic encoder is in `scripts/build_deck_code.py`.

Suggested install patterns:

- Codex-style skill folders: copy `hearthstone-deck-builder/` to a configured skills directory such as `.codex/skills/` in a workspace or the user's global Codex skills directory.
- Claude Code skill/plugin folders: copy the same folder into the Claude skills/plugin location used by that installation.
- Cursor skill folders (same `SKILL.md` format): copy or symlink the folder into the personal `~/.cursor/skills/` directory or a project-level `.cursor/skills/` directory. Cursor's `skills-cursor` folder is reserved for synced built-in skills; put user skills under `skills/`.
- CLIs without native `SKILL.md` support: add a project instruction file such as `AGENTS.md`, `CLAUDE.md`, or `GEMINI.md` that says: “When asked to build a Hearthstone deck or deck code, read and follow `hearthstone-ai-coach/hearthstone-deck-builder/SKILL.md`.”

Do not duplicate or rewrite the deckstring algorithm in each CLI's prompt file. Point all CLIs at the same skill folder so fixes to the encoder stay shared.
