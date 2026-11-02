# Changelog

## Unreleased

- The pages drop the 0–5 lottery score, the Skip/Small/Standard tiers, and the best-lottery-shot cards. Each chain has a Paper test status card labelled "Not a pick, not a ranking". EST-01 and SW-01 near-misses list each registered check as passed or failed. A ticket is only an open registered test at its fixed 100 ADA paper. The risk line shows kill-line distance, the swarm flag (red, not a point), codenamed whale flow, and holders. X buzz is not a score.
- Each prefilter run appends holder counts and kill lines for the EST-01 universe to `solada-prefilter/est_universe_log.jsonl`, and writes `solada-prefilter/x_buzz_shortlist.json` so the X job can query the universe plus whale-ledger tickers inside the existing 10-row Cardano budget. Neither file changes a kill line, a verdict, or a ticket. A Solana whale-flow ledger is not started: there is no codename-to-wallet map, and the frozen v6.5 path does not import the TODO module.
- The PR #75 dashboard stays published at `archive/v75/`. The new pages link to it as Previous version.
- The desk is four pages: Home, Cardano, Solana, and NFT. Fresh briefs and the Cardano failure warning sit on Home. `line` and the cursor follow the page: cyan, Cardano blue, Solana green, NFT pink. Cards, the header, and the two-column rhythm follow baseline-desk. Solana books show the day-14 expectancy as a percent per trade. The previous dashboard stays at `legacy.html`, titled Sentinel.
- Daily briefs publish from the desk log again. The external daily run stopped adding `briefs/YYYY-MM-DD.json` after 2026-10-19 while it kept writing `log.json`. `scripts/publish_briefs.py` fills the missing days and writes `briefs/manifest.json`, which the page reads from desk-data.
- Pages publishes `x_buzz.json`, `solada-prefilter/whale_ledger.json`, and `solada-prefilter/kill_status.json`. Those files lived only on desk-data, so the site URLs 404'd.
- NFT floors convert with `dashboard/prices.json` (CoinGecko, fetched when the site is built). The browser does not call a price host.
