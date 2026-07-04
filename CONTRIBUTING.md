# Contributing

Thanks for considering a contribution. This project is intentionally small and dependency-free.

## Development setup

```bash
git clone https://github.com/barnabys-drew/hearthstone-ai-cli-skills.git
cd hearthstone-ai-cli-skills
python3 -m unittest discover -s tests
```

## Guidelines

- Keep scripts dependency-free unless there is a strong reason to add a package.
- Do not commit real collection exports, cookies, account IDs, or private deck-tracker data.
- Prefer deterministic tests with synthetic DBF IDs.
- Keep `SKILL.md` files concise; put long examples or references in `references/` or `docs/`.
- If changing deckstring encoding/decoding, add or update a round-trip test.

## Pull request checklist

- [ ] Tests pass with `python3 -m unittest discover -s tests`.
- [ ] No private collection/account data is included.
- [ ] README or docs are updated if behavior changed.
