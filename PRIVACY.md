# Privacy

This repo is public and published via GitHub Pages. It holds **paper and research data only**.

- Never commit owner wallet addresses, stake keys, real holdings, balances, or real transactions.
- Keep those in private bot memory, not in any file here (including notes, headlines, history and followups).
- Third-party research data (token mints, policy IDs, platform/pool addresses, launcher/sniper keys) is fine.
- Run `scripts/privacy-guard.sh` (and `python3 scripts/validate-desks.py` for the format check, workflow `schema-check.yml`) before committing (no arguments = staged files, or all tracked files if nothing is staged). CI (`.github/workflows/privacy-guard.yml`) runs it over all tracked files on every push and PR to `main` and fails the check on any match; the optional `PRIVACY_GUARD_PATTERNS` repo secret adds extra patterns there.
- Add full private addresses as extra patterns locally in `.privacy-patterns` (gitignored, one regex per line) or the `PRIVACY_GUARD_PATTERNS` env var / repo secret (newline separated). Never commit them.
