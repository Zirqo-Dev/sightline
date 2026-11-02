# Changelog

## Unreleased

- The site is one scrolling page (Sentinel's pick, Cardano lanes, paper tests, whale codenames, Solana F/R/S, X buzz, NFT watchlist, latest desk note). `line` and the blinking `_` stay `#3d8bff`. Legacy stays at `legacy.html` and says Sentinel.
- Daily briefs publish again from the desk log when `briefs/YYYY-MM-DD.json` is missing. The brief index is the union of main and desk-data. Pages copies `x_buzz.json`, `solada-prefilter/whale_ledger.json`, and `solada-prefilter/kill_status.json` into the site. NFT floors convert with a USD quote stored in `prices.json`.

- Cardano, Home, Solana, NFT, and Briefs share `preview/theme.css`. The logo stays `>sightline_`; only `line` and the `_` cursor use `--sl-wordmark` (one colour per tab). On Cardano that ending is desk blue (`#3d8bff`); lime stays the outline accent. Solana F/R/S numbers follow `solana-desk/specs/day14_verdict.py` (ticket expectancy, void window, Nov 11 cutoff, `VERDICT NOT_FINAL` until then). Coin and NFT rows use `dashboard/icons/icons.json`; a coloured initial is only the fallback. NFT amounts are USD only. Research only.
