# Changelog

## Unreleased

- The desk is one scrolling page. Sentinel, Cardano lanes, paper tests, whale codenames, the frozen Solana day-14 books, measured X buzz, the KING watchlist in USD, and a fresh desk note share the page. `line` and the `_` cursor stay `#3d8bff`. Lime is the outline. Amber is the watch pill and the Cardano failure warning. The previous dashboard stays at `legacy.html`, titled Sentinel.
- Daily briefs publish from the desk log again. The external daily run stopped adding `briefs/YYYY-MM-DD.json` after 2026-10-19 while it kept writing `log.json`. `scripts/publish_briefs.py` fills the missing days and writes `briefs/manifest.json`, which the page reads from desk-data.
- Pages publishes `x_buzz.json`, `solada-prefilter/whale_ledger.json`, and `solada-prefilter/kill_status.json`. Those files lived only on desk-data, so the site URLs 404'd.
- NFT floors convert with `dashboard/prices.json` (CoinGecko, fetched when the site is built). The browser does not call a price host.
