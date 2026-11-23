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

`python3 -m tools.replay --series PATH --rules FILE` replays pluggable entry rules over `manifest-*` and `pool-snaps-*` files. A row is used only when `sol` and `token` are numbers. Death-log files and death-log rows are skipped. An entry reads that bar only: it does not require a later bar to exist. The default exit mode is `desk_v65`, which calls `desk.exits.check` (the 120-minute time stop keeps a position that is up at least 10%, and `no_route` / stale marks follow that function). A gap longer than `max_gap_s` (default: the desk's outage limit) or the end of the tape closes at the last known mark. A dead pool closes as `pool_gone`. The book uses the desk's starting cash, enters only when cash covers `entry_cost`, and stops at the open cap and the per-day cap. The null is 1000 seeded draws from the same filtered names, matched by UTC day and hour, with those same caps and no overlapping mint. The report gives the rule's percentile against that null, a bootstrap confidence interval clustered by day (or by mint when the trades share a day), and the Kish effective n. Fills use a constant-product quote for the paper ticket, then the desk's `v3-exit-aware` haircut and network fee. The desk cost functions do not include an LP fee. `lp_fee_bps` defaults to 0 for that reason: the synthetic quote does not add a pool fee unless the config sets one. That 0 is not a measured pool fee. The loader refuses book ledgers, fill logs, and lst-count captures. Until 2026-11-25T15:00:00Z it also refuses bars at or after 2026-10-26T00:00:00Z and any `solana-desk/v2` path, unless `--allow-blinded-window` is set. `tools/replay/example-rules.json` is a config template. Tests under `tools/replay/tests/` use synthetic fixtures only.

`python3 tools/idle_books_check.py --data-dir DIR` reads paper-book state. It prints one `IDLE` line when a book had passing leads and zero entries for 24 hours, or when cash is below `entry_cost` (the same check the fill uses, not the bare ticket). The line says `cash_below_floor=yes` or `cash_below_floor=no` and does not print a cash amount. A book that is quiet only because it is at the open cap or the daily cap prints `CAPPED` instead. Missing or malformed state exits 2. It is not wired into a workflow.
