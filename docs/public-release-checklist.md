# Public release checklist

Use this before flipping the GitHub repository from private to public.

## Safety and privacy

- [ ] Run `git status --short` and verify no local collection files are staged.
- [ ] Search for secrets or account identifiers:
  ```bash
  grep -RInE 'account_lo|Cookie:|hsreplay|token|session|battle.net' . \
    --exclude-dir=.git --exclude='*.md'
  ```
- [ ] Confirm examples are synthetic and do not contain real account data.
- [ ] Confirm screenshots, if any are added later, do not show account details.

## Quality

- [ ] Run tests:
  ```bash
  python3 -m unittest discover -s tests
  python3 hearthstone-deck-builder/scripts/build_deck_code.py --selftest
  ```
- [ ] Verify the README quick-start works from a fresh clone.
- [ ] Check `SKILL.md` descriptions trigger the right AI behavior.
- [ ] Test one real deckstring from a current Standard deck source.

## GitHub polish

- [ ] Add a repository description.
- [ ] Add topics such as `hearthstone`, `ai-skills`, `deckstring`, `codex`, `cursor`, `claude-code`.
- [ ] Confirm Actions are green after making the repo public.
- [ ] Decide whether to keep issue discussions enabled.

## Disclaimer

- [ ] Keep the unofficial fan/tooling disclaimer in the README.
- [ ] Do not use Blizzard logos or assets unless you have rights to them.
