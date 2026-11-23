# Sightline

Paper-trading and research desks. Not financial advice. No real positions.

https://zirqo-dev.github.io/sightline/

The site root is the phone-first shell (`index.html`, `revamp/app.js`, `revamp/app.css`). It shows the desks, the tested book against its twin, wallets, experiment duration, example equity, an example open trade, the archived v6.5 card, and freshness stamps. Race wallets, including R1–R5 and the SOL-hold benchmark, are read in the browser from the `races-data` branch (`summary.json`, `equity.jsonl`, `positions.json`, and one `trades.jsonl`). The lead is `official_net_pct`. R5 says awaiting first run until the summary lists it. The field names live in `revamp/races.js`.

The previous four-page site (Home, Cardano, Solana, NFT) stays at [legacy/index.html](legacy/index.html). It still reads `desks.json` and each desk's JSON from the same origin, and newer copies from the `desk-data` branch when those are fresher. The build copies `x_buzz.json`, `whale_ledger.json`, and `kill_status.json` to the site root and under `solada/`, and also keeps `solada-prefilter/whale_ledger.json` and `solada-prefilter/kill_status.json`. The older dashboard stays at `legacy.html`. `preview.html` is the same four-page page with a preview badge. Legacy panel layout: [dashboard/PLUGINS.md](dashboard/PLUGINS.md). File format: [schema/README.md](schema/README.md). Privacy: [PRIVACY.md](PRIVACY.md).

The older NFT finder still runs at [nft/site/](nft/site/). After deploy: `https://zirqo-dev.github.io/sightline/nft/site/`. Paste an OpenSea key in that page only. Never commit it.

Experimental Solana early-buyer forensics lives in [sol_forensics/](sol_forensics/README.md). Reports go in `solada/forensics/`. Research only: no trading and no keys. The desk page shows an Experimental forensics panel; the uncalibrated labels are not signals.

## Build

```bash
python3 scripts/build-site.py
python3 -m http.server -d dist
```

The build uses the Python standard library only. It writes `dist/`, generates each desk's `briefs/index.json` from `briefs/YYYY-MM-DD.json`, and includes every file in `dashboard/panels/`. Adding a panel does not require an `index.html` edit: the build injects those script tags into `legacy.html`. Published `index.html` gets a `?v=` query on `revamp/` scripts and styles, and on `preview/fonts.css`. `preview.html` and `legacy/index.html` get a `?v=` query on `preview/app.css` and `preview/app.js`. `legacy.html` gets its own `?v=` on `dashboard/` assets, so a design edit does not retint the previous dashboard.

`node scripts/smoke-dashboard.mjs` checks that the dashboard scripts parse, that P&L stays per unit, that distance-to-kill is computed correctly, that a wallet snippet always renders the same compact tag and color, and that pulse, radar, and data-age behave against `scripts/fixtures/pulse-example.json`. Number checks use `scripts/fixtures/dashboard-smoke.json`. Live desk files only get a shape check, so a bot opening or closing a ticket, or not having written `pulse.json` yet, does not fail the deploy.

Icons live in `dashboard/icons/` and are loaded from the site itself. `python3 tools/resolve-icons.py .` fills icons that are not committed yet. It does not run as part of `scripts/build-site.py`.

## Publish

`.github/workflows/pages.yml` runs on every push to `main`. It runs `scripts/privacy-guard.sh` and `scripts/validate-desks.py` first, then builds `dist/` and deploys with `actions/upload-pages-artifact` and `actions/deploy-pages` only if both checks pass.

The repository Pages source has to be **GitHub Actions** (Settings → Pages → Build and deployment → Source). Until that setting is changed, Pages still deploys the branch itself and will not run this workflow's deploy job.

## Cloudflare later

`dist/` is plain static files and the pages use relative URLs. Point Cloudflare Pages at this repo with build command `python3 scripts/build-site.py` and output directory `dist`, then put Cloudflare Access in front. Make the GitHub repo private too, or the JSON stays readable on GitHub. No frontend change.

## Offline checks

Two read-only tools sit beside the desks. They are not on a schedule, they do not change desk, book, config, fill, or heartbeat code, and they do not open the network.

`python3 -m tools.replay --series PATH --rules FILE` replays pluggable entry rules over `manifest-*` and `pool-snaps-*` files. A row with numeric `sol` and `token` is a price bar. A row with null reserves and an `error` (the v2 dead-pool line, including `no_pool`) is kept and passed to `desk.exits.check`, so the close is `pool_gone` or `no_route` and the booked result is `-entry_cost`. Death-log files and death-log rows are skipped. An entry reads that bar only. The default exit mode is `desk_v65`. A gap longer than `max_gap_s` (default: the desk's outage limit), while other names keep the tape moving, does not book a total loss. That position is unresolved: the lower bound is `-entry_cost` and the upper bound is the last mark net of exit costs. Positions still open when the tape ends are marked the same way on the upper bound only, the way the desk values an open ticket. The report gives the rule net, the null percentile, and the bootstrap interval at both bounds, plus `unresolved_n`. The bounds label is `BOUNDS_AGREE` or `BOUNDS_DISAGREE`. The verdict is `BEATS_NULL` when both bound percentiles are at least 95, `WORSE` when both are at most 5, and `NO_EVIDENCE` otherwise. The report names `net_lower`, `net_upper`, `percentile_lower`, and `percentile_upper`. It also counts closes by reason. An unresolved name leaves the open-cap slot at `unresolved_at`; only a live position counts toward the cap of 4. The book uses the desk's starting cash and enters only when cash covers `entry_cost` and the row is desk-enterable. `entry_match` still applies the S2 all-pass rule whenever a bar has a `rules` map, including when the config does not set `rules_all_pass`. That is not the v6.5 `EXEC` filter. Rule entries and the null both require a desk-enterable scan row: a `rules` map, pool SOL of at least 10, a ticket of at most 5% of that SOL, at least one sell in the past hour, and not a follow snapshot. The null is 1000 seeded draws from that set, without the rule's extra filter, matched by UTC day and hour, with the same caps and no overlapping mint. A `desk_v65` report prints the unresolved share and notes that holds past 2h cannot be resolved on v2 data. `desk_v65_2h` forces the 120-minute exit and is labelled `DEVIATION from v6.5: forced 120m exit`. The percentile and the bootstrap interval both use the sum of net PnL. The interval resamples holds that overlap in time across mints, and the report warns when there are fewer than 5 such clusters. Effective n is the Kish size of those overlap groups. Every report starts with a proxy-inputs line: marks are pool math, liquidity is the bar's SOL, `no_route_3x` cannot fire, marks exist only at snapshots, and entries are the v2 S2 rules.

Pool math is not Jupiter's `outAmount`. That quote is already net of the pool fee, so each side of the replay adds the canonical PumpSwap total from the [pump.fun fees page](https://pump.fun/docs/fees) (last updated 16 November 2026). With no `market_cap_sol` on the bar the fee is 125 bps (1.25%), the total for a SOL market cap under 420. When `market_cap_sol` is present the replay uses that page's SOL ladder, which steps down to 30 bps (0.30%) at 98,240 SOL and above. `swap_fee_bps` on the rule replaces the ladder for a run. The desk `COST` block still has no separate pool-fee field; this fee is the one the Jupiter quote would already have taken out.

`replay()` and `_run_book` refuse a bar at or after 2026-10-26T00:00:00Z while the real UTC clock is before 2026-11-25T15:00:00Z. The cutoff and the start instant are default arguments on the guard, so assigning the module names does not move them. A bar time is coerced with `int()`; a value that cannot be coerced is refused. Patching the function's `__defaults__` cannot be fully prevented in Python. A caller cannot pass a different clock. The loader resolves paths with `realpath` before the `solana-desk/v2` check. It also refuses book ledgers, fill logs, and lst-count captures. `tools/replay/example-rules.json` is a config template. Tests under `tools/replay/tests/` use synthetic fixtures only.

`python3 tools/idle_books_check.py --data-dir DIR` reads paper-book state and `tasks.csv`. A missing `tasks.csv`, a missing pool size, or malformed state exits 2. The floor is `entry_cost` for the book's own pool size, not the bare ticket and not a stand-in pool. The line says `cash_below_floor=yes` or `cash_below_floor=no` and does not print a cash amount. A book that is quiet only because it is at the open cap or the daily cap prints `CAPPED` instead. It is not wired into a workflow.
