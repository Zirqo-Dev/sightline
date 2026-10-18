# Privacy

This repo is public and published via GitHub Pages. It holds **paper and research data only**.

- Never commit owner wallet addresses, stake keys, real holdings, balances, or real transactions.
- Keep those in private bot memory, not in any file here (including notes, headlines, history and followups).
- Third-party research data (token mints, policy IDs, platform/pool addresses, launcher/sniper keys) is fine.
- Run `scripts/privacy-guard.sh` before committing. CI runs it on every push and PR to `main`.
- Add full private addresses as extra patterns locally in `.privacy-patterns` (gitignored, one regex per line) or the `PRIVACY_GUARD_PATTERNS` env var / repo secret (newline separated). Never commit them.
