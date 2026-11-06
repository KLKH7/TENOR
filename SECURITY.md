# Security

## What this repo never contains

- **No secrets.** API keys are read only from environment variables (`ANTHROPIC_API_KEY`, `GRANOLA_API_KEY`) or a local, git-ignored `.env`. They are never logged, printed, repr'd or persisted; tests assert this with a fake key.
- **No real meeting data.** The author's own Granola sync (`data/real.db`, `data/real/`) is git-ignored and was never committed. Everything under `data/fixtures/` is synthetic.
- **No Granola writes.** The MCP source is read-only by construction: it never calls Granola's MCP write tools.

`scripts/prepublish_check.py` enforces the first two before every release. It scans every tracked file and every blob in git history for API-key patterns, `.env` content, `data/real` paths and non-synthetic email addresses, and fails on tracked files over 5 MB:

```bash
uv run python scripts/prepublish_check.py --all
```

## Reporting a problem

If you find a secret, personal data, or a vulnerability in this repo, please **don't open a public issue**. Use GitHub's private vulnerability reporting (the "Report a vulnerability" button under the repository's Security tab) and include the file, commit and what you found. I'll acknowledge within 7 days.

This is a research prototype, not a hosted service: there is no production deployment to attack. The most likely issues are leaked data in history and unsafe handling of tokens in the sync code (`packages/tenor/sources/`).
