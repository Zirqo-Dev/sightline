# Solana Paper Desk

A **paper-only** trading desk for Solana tokens. It never holds keys, never signs
transactions, and never touches a wallet: every "trade" is a simulated entry/exit
recorded in a ledger with a fixed 0.5 SOL paper ticket.

- `desk/` — config, skip rules (`skips.py`), books F/R/S (`books.py`), exits, ledger, report.
- `tests/` — unittest suite run against redacted fixtures (third-party holder wallets replaced with placeholders).
- `docs/` — design plan v6.3.
- `CHANGELOG.md`, `findings.md` — build notes and API findings.

## How it runs
A plain Python loop (no AI) runs on the Grok Bot cloud box every 10-15 minutes
with a lock file. GitHub Actions is used only to build/deploy Pages.

## Where data goes
All generated files (tasks/trades CSVs, state, daily summaries, `paper.json`,
`log.json`, briefs, review notes, health) are pushed to the **`desk-data`** branch,
at most hourly. `main` holds code only and changes only via PRs Bousky merges.

## Tests
    cd solana-desk && python3 -m unittest discover -s tests -t .
