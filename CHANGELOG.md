# Changelog

## Unreleased

- The desk is four pages: Home, Cardano, Solana, and NFT. Fresh briefs and the Cardano failure warning sit on Home. `line` and the cursor follow the page: cyan, Cardano blue, Solana green, NFT pink. Cards, the header, and the two-column rhythm follow baseline-desk. Solana books show the day-14 expectancy as a percent per trade. The previous dashboard stays at `legacy.html`, titled Sentinel.
- Daily briefs publish from the desk log again. The external daily run stopped adding `briefs/YYYY-MM-DD.json` after 2026-10-19 while it kept writing `log.json`. `scripts/publish_briefs.py` fills the missing days and writes `briefs/manifest.json`, which the page reads from desk-data.
- Pages publishes `x_buzz.json`, `solada-prefilter/whale_ledger.json`, and `solada-prefilter/kill_status.json`. Those files lived only on desk-data, so the site URLs 404'd.
- NFT floors convert with `dashboard/prices.json` (CoinGecko, fetched when the site is built). The browser does not call a price host.
