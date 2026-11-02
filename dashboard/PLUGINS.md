# How panels and plugins work (Sightline dashboard)

The dashboard has no desk-specific code. It reads `desks.json` to find the desks, then each desk's optional `panels.json` to decide what to show and in what order. Navigation, Home and the Briefs archive are built from `desks.json`.

**Bots add panels by editing one file in their own folder.** Snek Sentinel edits `solada/panels.json`. KING edits `nft/panels.json`. Each entry is a panel:

| Field | Meaning |
|---|---|
| `type` | Which built-in component draws it (list below). |
| `title` | Heading shown on the panel. |
| `data` | A JSON file relative to the desk folder, or a list of files (both paper books, for example). A path ending in `/` is a folder (used by `briefs`). |
| `path` | Optional. Where inside that file the data lives, dot-separated. Example: `kill_lines` or `runs.0.watchlist`. |
| `options` | Optional. Extras such as columns, labels, a row limit, a badge column, chart fields, embed height. |
| `span` | Optional. Grid width 12, 8, 6 or 4. Default is full width. |

If a desk has no `panels.json`, it gets a default page from its standard files: latest run, scoreboard, open tickets, closed tickets, rules, briefs. A new desk added to `desks.json` shows up in the navigation, on Home and in Briefs with no other change.

```json
{
  "panels": [
    {"type": "headline", "title": "Latest tape", "data": "log.json"},
    {"type": "tickets", "title": "Open tickets", "data": "paper.json"},
    {"type": "table", "title": "Floor watch", "data": "log.json", "path": "runs.0.watchlist",
     "options": {"columns": ["name", "chain", "status"], "badge": "status"}},
    {"type": "notes", "title": "Desk notes", "data": "notes.json"}
  ]
}
```

A bot can show something new by writing a JSON file in its folder and pointing a `table`, `notes`, `stat-cards` or `chart` panel at it. That does not need a dashboard code change.

## Built-in panel types

One file each in `dashboard/panels/`. Each calls `S.register`.

| Type | What it draws |
|---|---|
| `headline` | Latest run headline and time. |
| `radar` | Early-runner cards: heat ring, metric tiles, sparkline, age, warning badges, momentum map. Quiet line when the file is missing. A `table` panel whose `data` is the pulse file and whose `path` is `radar` draws this same view. |
| `scoreboard` | Open, closed, win rate, and realized P&L **per unit**, plus one row per book. |
| `tickets` | Open tickets, closest to the kill line first. |
| `closed` | Closed tickets with percent and P&L in that ticket's unit. |
| `rules` | Rules learned, newest first. Plain title, category, book, evidence chips. `options.limit` hides the rest behind Show all (Solada uses 8). |
| `briefs` | Daily briefs for the desk. Reads the build-generated `briefs/index.json`. |
| `table` | Any list of objects, or a key/value object. `options.columns`, `labels`, `limit`, `badge`. |
| `stat-cards` | One card per field of an object. |
| `notes` | A string, or an object of named sections. |
| `chart` | A simple line. `options.x` (default `t` or `at`) and `options.y`. |
| `embed` | Another page, collapsed to one line (`details`). `options.url` is relative to the site root (`nft/site/`) or absolute. `options.preview` is an image in the desk folder, shown until the panel is opened and the preview is clicked. `options.height` is pixels. |
| `solada-intel` | Solada intel: 24h flag strip from `solada-prefilter/flags.json`, Cardano top 10 from `log.cardano_top10`, Grumpy Walrus card (ASCEND/STRIKE first, hidden when absent), per-ticker drill-down (pulse sparklines, kill-line bar, first-block buyers held/exited, cluster timeline), cluster heat grid, pattern scoreboard. Every part has an empty state. Wallets render as tags only. |
| `coming-soon` | A reserved slot. `options.text` is the message. |

## Cardano tab (v2)

The live Cardano tab (`preview/cardano-v2.js`, route `#/cardano`) is two boards: Top 10 Runners, then Top 10 Established. It reads the desk-data copies of `solada/log.json`, `solada/pulse.json`, `solada/paper.json`, `solada-prefilter/flags.json`, `solada-prefilter/dev_checks.json`, `solada-prefilter/vetoes.json`, `solada-prefilter/whale_ledger.json`, `solada-prefilter/big_moves.json`, and `x_buzz.json`, falling back to main. A missing figure is omitted or shown as "?". The tab does not invent a Sentinel pick or a percent.

Runners are fresh launches (`cardano_v2.launches` and verdicts whose `launchpad` is not `established` or `minswap`), ranked by the newest percent move. Established is `cardano_top10` plus verdicts tagged `established` or `minswap`, in the desk's high-cap order. The row pill is SW-01 only for an open SW-01 ticket, otherwise Lottery, Watch, Passed·moving (a pass whose price is up), or Pass. ▲ renders only when both holders and volume rose over the last 6h. A sparkline renders only when price or market-cap history is fresh enough to draw.

Sentinel's pick cards read open tickets on `paper.tests` SW-01 and EST-01 (and `paper.open` or `log.picks` only when the id says SW-01 or EST-01). SW-01 sits on the runners board (100 ADA, 48h, from the registered test). EST-01 sits on the established board (100 ADA, 7d). A closed ticket is not a pick. The why-sheet checklist uses bonded, rule k, swarm flags, a whale buy after 24h, dev sold, rugger vetoes, and X buzz. Unknown checks stay "?".

### Fields the board reads

| Source | What the tab uses |
|---|---|
| `log.verdicts[]` | `ticker`, `verdict`, `launchpad`, `pool_ada`, `holders`, `mcap_ada`, `why` / `reasons` (for a real percent phrase and a whale sentence), `whale_line` / `whale_activity`, `kill_value`, `kill_limit`, `pct_from_line`. |
| `log.cardano_top10[]` | High-cap order, pool, holders, mcap. History of the same rows is the sparkline when it is fresh. |
| `paper.tests` SW-01 / EST-01 | Open `tickets[]` only. Closed tickets are skipped. |
| `flags.json` | `coordinated_buys` and `fresh_wallet_swarm` set the swarm check. Tags are not printed. |
| `dev_checks.json` | Dev sold and fresh funded. A `w-` tag is not printed. |
| `vetoes.json` | `known_rugger` codes. No row for that token stays "?". |
| `whale_ledger.json` `rows[]` | Buy or sell direction after launch+24h, and a trend. Balances, positions, and quantities are not shown. |
| `x_buzz.json` | Cardano `velocity.ratio_1h_vs_prev_hr`. Missing ratio stays "?". |

Kill distance is `pct_from_line` when the desk wrote it. The old heat grid, radar cards, and money-flow chart are not drawn.

### Optional `log.cardano_v2`

Write this object on `solada/log.json` (desk-data). Do not put it in `panels.json`: that file is site config and is read from `main`, not from desk-data. Every key is optional. The board uses `launches`, `rotation.trends`, and `swarms`. `clusters` and `heat` are not drawn. A launch with `example: true` is skipped. `holders_example` or `pool_example` drops that number.

```json
{
  "cardano_v2": {
    "entry_bar": { "holders": 120, "pool_ada": 25000 },
    "launchpads": [
      { "id": "solada", "label": "Solada", "launches": 74, "graduated": 5 }
    ],
    "launches": [
      {
        "ticker": "GHOST", "launchpad": "snek", "graduated": false,
        "age": "9h", "holders": 142, "pool_ada": 31200,
        "swarm": false, "sniper": false, "example": false
      }
    ],
    "rotation": {
      "who": "Grumpy Walrus",
      "window": "Last 24h",
      "from": "2026-10-28T19:34:00-04:00",
      "to": "2026-10-29T08:03:00-04:00",
      "out": [{ "token": "SNEK", "detail": "sold 1M", "ada": 2978 }],
      "in": [{ "token": "ASCEND", "ada": 1521 }],
      "takeaway": "Selling SNEK → rotating into ASCEND.",
      "trends": [{ "token": "ASCEND", "trend": "accumulating" }]
    },
    "swarms": [
      {
        "name": "Swarm group A", "label": "repeat early buyers", "wallets": 14,
        "last_seen": ["FOSHIZLE", "MSNEK"],
        "launches": [{ "ticker": "RTB", "at": "2026-10-29T06:10:00-04:00" }],
        "note": "When this group jumps in, it's usually a swarm launch → Pass."
      }
    ],
    "snipers": { "tickers": ["CHEESE", "INTEL"], "note": "first-block buyers fully exited." },
    "hot": [{ "ticker": "MEPE", "line": "Whale added ADA and a swarm bought at launch.", "note": "" }],
    "clusters": [
      { "ticker": "TCM", "tone": "r", "pill": "coordinated buys", "summary": "Coordinated buys + 7 repeat early buyers", "wallets": ["w-8315deec"] }
    ],
    "heat": {
      "tokens": ["SLIP", "CHEESE"],
      "wallets": [{ "tag": "c66a", "cells": { "CHEESE": { "ada": 980, "sniper": true } } }]
    }
  }
}
```

`launches` / `graduated` may also be named `launches_7d` / `graduated_7d`. `rotation.in` may be named `inn`. `trend` is `accumulating`, `trimming`, `exiting`, or `flat`. `wallets` on a swarm is a count. On a cluster it is a list of `w-` tags; anything else is dropped. Heat `tag` is 4 hex characters, or a `w-` tag (shown as its first 4 hex). A full address is dropped.

Launch progress is the average of `holders / entry_bar.holders` and `pool_ada / entry_bar.pool_ada`, each capped at 1. Both at or above the bar, with `swarm` and `sniper` false, is "Getting traction". `swarm: true` is "Swarm launch → Pass". Otherwise the card says "Watching · N% of the way to the bar".

### Optional fields on each `verdicts[]` row

These override the parsed prose and `kill_status.json` for that ticker. `kill_line` stays the prose sentence the desk already writes. The numeric threshold is `kill_limit` (a number). A numeric `kill_line` is also accepted as the threshold.

| Field | Meaning |
|---|---|
| `launchpad` | Badge only. `solada` → Solada. `snek` → Snek.fun. `new` or `unknown` → New launch (a brand-new token from another pad). `established`, `minswap`, missing, null, or empty → no badge. `established` is the preferred value for older Minswap pools and OG projects (STRIKE, ASCEND, FLOW). `minswap` also draws no badge, because those tokens are the norm. Radar rows are new launches, so a missing or unknown pad there still shows New launch. |
| `kill_metric` | `pool_ada` (alias `ada_side`), `holders`, `mcap` (alias `mcap_ada`), or `curve`. |
| `kill_value` | Current reading of that metric. |
| `kill_limit` | Threshold. Also accepted as `kill_threshold`. |
| `pct_from_line` | Percent above the line. Negative means under it. Computed when omitted. |
| `why` | Up to 3 bullets. Falls back to `reasons`. |
| `whale_line` | Whale sentence. Falls back to `whale_activity`. |
| `whale_trend` | Not drawn on the card. Put per-token trends on `cardano_v2.rotation.trends`. |

A populated example lives in `scripts/fixtures/cardano-v2.json`. The desk does not need to write every block before the tab ships: each section hides itself behind "No data yet".

## P&L and distance to kill

Realized P&L is summed **inside one unit only**. ADA, ADA-equivalent and USD each get their own total. The dashboard never adds those together.

Distance to kill uses numeric fields on `kill_line` when they match a current reading. A field named `holders_below` (also `_under`, `_lt`, and `_above` / `_over` / `_gt`) is compared with the same name on the latest mark, falling back to entry. When the desk pulse file has points for that ticker, the latest pulse metrics override those readings, and the open-ticket sparkline uses the pulse series. The closest line is the one with the least room left. If no numeric field matches, the text is parsed for comparisons such as `floor < 0.70`, `holders < 97,082` or `mcap < 450k`.

## Pulse, data age, and Run now

Bots may write the pulse file named by `pulse` in `desks.json`, or `<desk>/pulse.json` when that field is absent (schema `schema/pulse.schema.json`): an hourly window of `{t, ticker, metrics}` points plus a `radar` list. Optional `factors`, `score_max`, and `flags` on each radar row drive the cards; without them the page parses `signals[]`. A `table` panel with `path` `latest` stays a table. `path` `radar` on that same file draws the radar cards, so the desk does not have to change `panels.json`. Older months can sit in `<desk>/archive/pulse-YYYY-MM.json` with the same shape. Both are optional. A missing file does not fail the build or the page.

The header and each desk show `updated 12m ago` from the newer of `log.asof` and `pulse.updated_at`. It turns amber after 90 minutes and red after 6 hours, and the browser updates the wording on its own.

Each desk page, and each desk on Home, has a Run now button. `desks.json` supplies `run.url` and `run.label`. The button opens that URL in a new tab. It does not contain a token or secret, and it does not call a webhook from the browser. If `run.url` is missing the button is disabled and its tooltip says `not wired yet`. After a click the page shows `run requested, refresh in a few minutes` until reload. The default URLs are the desk's GitHub Actions workflow (`run-solada.yml`, `run-nft.yml`), which only a logged-in user with permission can dispatch.

## Daily briefs index

Bots write one file per day: `<desk>/briefs/YYYY-MM-DD.json`. They do not maintain an index. `scripts/publish_briefs.py` writes a brief for a day the log already covered when that file is missing, and refreshes only briefs it generated. `scripts/build-site.py` scans the brief files (main, then the desk-data overlay) and writes `<desk>/briefs/index.json`. The page reads that index from desk-data and from the site and keeps every date either side lists.

## Adding a component

Drop one file into `dashboard/panels/`, for example `floor-chart.js`:

```js
S.register("floor-chart", {
  render({ data, opt }) { return "<svg>…</svg>"; }
});
```

Do not edit `index.html` to register a panel. The live page is the redesigned shell. The build lists every `dashboard/panels/*.js` file and adds the script tags to `legacy.html`, the previous dashboard. After the next build, any desk can use `"type": "floor-chart"` in its `panels.json`.

If a desk lists a type that is not installed, that panel shows a short "not installed" line and the rest of the page works. Solada's sample layout does this with `heatmap`.

Two panels on the same desk with the same `type`, `data` and `path` are drawn once. The duplicate is counted in the desk subtitle.

## Icons, phone rows, wallet tags

Icons are local files under `dashboard/icons/`, listed in `dashboard/icons/icons.json`. The browser does not call an icon API. `tools/resolve-icons.py` fills anything missing and leaves a committed image in place unless you pass `--refresh`. A failed fetch does not replace a good file, and the site build does not run the resolver. CoinGecko coin search is only used for the platform token SOLADA, matched by name and symbol, not by a generic ticker.

On a phone (600px and under), tables become two-line rows. Level and status pills stay on one line. Long cells ellipsize; tap the row to read the rest.

Wallet addresses, stake keys, key fragments and mints in notes, headlines, rules and other prose render as a compact tag: the first 7 characters, an ellipsis, then the last 4 (`addr1q9…wwut`). A fragment of 12 characters or fewer, such as `u8a2m6`, stays whole. The tag color is a hash of the normalized value, so the same wallet looks the same on every desk. Hover or tap shows the snippet exactly as it is stored. Tags do not wrap. The owner's wallet is not masked here; `scripts/privacy-guard.sh` still blocks it before a deploy.

## Build and hosting

From the repo root:

```bash
python3 scripts/build-site.py
python3 -m http.server -d dist
```

`dist/` is a static folder: `index.html` (redesigned shell), `preview.html`, `legacy.html`, `preview/`, `dashboard/`, `desks.json`, and each desk's JSON, including the older NFT page at `nft/site/`. The browser loads desk JSON from the same origin with relative URLs. `index.html` and `preview.html` reference `preview/app.css` and `preview/app.js` with a content hash query so GitHub Pages does not keep a stale shell.

### Desk data comes from the `desk-data` branch

Since Oct 24 `main` only takes PRs, so the desk bots push their data to the `desk-data` branch. `S.get()` in `dashboard/core.js` reads desk data JSON (each desk's `log`, `pulse`, `paper` books, briefs, and `nft/site/intel.json` / `watchlist.json` via `window.sightlineData` in `nft/site/index.html`) from `https://raw.githubusercontent.com/Zirqo-Dev/sightline/desk-data/<path>?ts=<now>` and the same-origin Pages copy from `main` in parallel. The desk-data copy wins; main's copy is used when the file is missing on desk-data (404/not JSON) or when main's `asof`/`updated_at` is strictly newer. The freshness stamp is computed from whatever was picked, so it reflects the newer data.

Code and config always come from `main`: `desks.json`, `panels.json`, `config.json`, icons and schema. `briefs/index.json` is the union of the desk-data list and the list built into the site, so a brief that exists on only one side still appears. `prices.json` is a USD quote fetched when the site or the pre-filter runs; the browser reads that file and does not call a price host. raw.githubusercontent has a ~5 minute CDN cache; the `?ts=` buster usually avoids it, but allow a few minutes. Set `window.SIGHTLINE_DESK_DATA = ""` before `core.js` to read main only (or set another base URL). Pages also copies the live feeds (`x_buzz.json`, `solada-prefilter/whale_ledger.json`, `solada-prefilter/kill_status.json`, and the other desk JSON the page reads) from desk-data into the artifact, because those files are not on main. A Pages build still only runs on a main push, so between deploys the browser keeps reading desk-data first.


GitHub Actions (`.github/workflows/pages.yml`) runs `scripts/privacy-guard.sh` and `scripts/validate-desks.py` on every push to `main`, then builds `dist/` and deploys it with `actions/upload-pages-artifact` and `actions/deploy-pages` only if both checks pass.

The repo's Pages source has to be **GitHub Actions**, not "Deploy from a branch". That is a setting on the repository (Settings → Pages → Build and deployment → Source).

### Moving to Cloudflare Pages and Access later

1. Create a Cloudflare Pages project from this repo. Build command: `python3 scripts/build-site.py`. Output directory: `dist`.
2. Put Cloudflare Access in front of that hostname and allow the owner's email.
3. Make this GitHub repo private as well. Otherwise the JSON stays readable on GitHub even if the site asks for a login.

No dashboard code change is required for that move.

## Safety

Panels only display files already in the public repo. The privacy check and the format check run before anything is published. Paper and research data only: never owner wallet addresses, stake keys, real holdings, balances or real trades.
