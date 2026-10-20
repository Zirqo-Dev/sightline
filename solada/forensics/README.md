# Solana forensics data

Research output for `sol_forensics` (see `../../sol_forensics/README.md`). Paper and on-chain research only. No owner wallets, no keys, no trading.

| Path | What it is |
|---|---|
| `config.json` | Thresholds, watchlist, sourced infrastructure list, stablecoin mints. The CEX list is empty on purpose. |
| `reports/<mint>.json` | Latest report for that mint. Overwritten each run. |
| `index.jsonl` | One summary line per run. |
| `cache/funding.json` | Wallet to first-funder cache. Third-party addresses from analysed coins only. |
| `panel.json` | Notes for the desk's Experimental forensics panel. Flag codes are not written here. |
| `VALIDATION.md` | Spot checks against public explorers. |

These files are not desk paper, logs, or pulse. `scripts/validate-desks.py` does not schema-check them. The privacy guard still scans them.
