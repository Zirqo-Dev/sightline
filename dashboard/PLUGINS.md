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
| `coming-soon` | A reserved slot. `options.text` is the message. |

## P&L and distance to kill

Realized P&L is summed **inside one unit only**. ADA, ADA-equivalent and USD each get their own total. The dashboard never adds those together.

Distance to kill uses numeric fields on `kill_line` when they match a current reading. A field named `holders_below` (also `_under`, `_lt`, and `_above` / `_over` / `_gt`) is compared with the same name on the latest mark, falling back to entry. When the desk pulse file has points for that ticker, the latest pulse metrics override those readings, and the open-ticket sparkline uses the pulse series. The closest line is the one with the least room left. If no numeric field matches, the text is parsed for comparisons such as `floor < 0.70`, `holders < 97,082` or `mcap < 450k`.

## Pulse, data age, and Run now

Bots may write the pulse file named by `pulse` in `desks.json`, or `<desk>/pulse.json` when that field is absent (schema `schema/pulse.schema.json`): an hourly window of `{t, ticker, metrics}` points plus a `radar` list. Optional `factors`, `score_max`, and `flags` on each radar row drive the cards; without them the page parses `signals[]`. A `table` panel with `path` `latest` stays a table. `path` `radar` on that same file draws the radar cards, so the desk does not have to change `panels.json`. Older months can sit in `<desk>/archive/pulse-YYYY-MM.json` with the same shape. Both are optional. A missing file does not fail the build or the page.

The header and each desk show `updated 12m ago` from the newer of `log.asof` and `pulse.updated_at`. It turns amber after 90 minutes and red after 6 hours, and the browser updates the wording on its own.

Each desk page, and each desk on Home, has a Run now button. `desks.json` supplies `run.url` and `run.label`. The button opens that URL in a new tab. It does not contain a token or secret, and it does not call a webhook from the browser. If `run.url` is missing the button is disabled and its tooltip says `not wired yet`. After a click the page shows `run requested, refresh in a few minutes` until reload. The default URLs are the desk's GitHub Actions workflow (`run-solada.yml`, `run-nft.yml`), which only a logged-in user with permission can dispatch.

## Daily briefs index

Bots write one file per day: `<desk>/briefs/YYYY-MM-DD.json`. They do not maintain an index. `scripts/build-site.py` scans those files and writes `<desk>/briefs/index.json` into the published site. The Briefs page and the `briefs` panel read that index.

## Adding a component

Drop one file into `dashboard/panels/`, for example `floor-chart.js`:

```js
S.register("floor-chart", {
  render({ data, opt }) { return "<svg>…</svg>"; }
});
```

Do not edit `index.html`. The build lists every `dashboard/panels/*.js` file and adds the script tags. After the next build, any desk can use `"type": "floor-chart"` in its `panels.json`.

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

`dist/` is a static folder: `index.html`, `dashboard/`, `desks.json`, and each desk's JSON, including the legacy page at `nft/site/`. The browser loads desk JSON from the same origin with relative URLs.

### Desk data comes from the `desk-data` branch

Since Oct 24 `main` only takes PRs, so the desk bots push their data to the `desk-data` branch. `S.get()` in `dashboard/core.js` reads desk data JSON (each desk's `log`, `pulse`, `paper` books, briefs, and `nft/site/intel.json` / `watchlist.json` via `window.sightlineData` in `nft/site/index.html`) from `https://raw.githubusercontent.com/Zirqo-Dev/sightline/desk-data/<path>?ts=<now>` and the same-origin Pages copy from `main` in parallel. The desk-data copy wins; main's copy is used when the file is missing on desk-data (404/not JSON) or when main's `asof`/`updated_at` is strictly newer. The freshness stamp is computed from whatever was picked, so it reflects the newer data.

Code and config always come from `main`: `desks.json`, `panels.json`, `config.json`, the build-generated `briefs/index.json` (so a brief must also be listed by a main build to appear; each listed brief is then read desk-data-first), icons and schema. raw.githubusercontent has a ~5 minute CDN cache; the `?ts=` buster usually avoids it, but allow a few minutes. Set `window.SIGHTLINE_DESK_DATA = ""` before `core.js` to read main only (or set another base URL). Why client-side rather than overlaying desk-data at build time: a Pages build is only triggered by `main` pushes, and pushes made with the workflow `GITHUB_TOKEN` do not trigger other workflows, so the overlay would go stale between deploys.


GitHub Actions (`.github/workflows/pages.yml`) runs `scripts/privacy-guard.sh` and `scripts/validate-desks.py` on every push to `main`, then builds `dist/` and deploys it with `actions/upload-pages-artifact` and `actions/deploy-pages` only if both checks pass.

The repo's Pages source has to be **GitHub Actions**, not "Deploy from a branch". That is a setting on the repository (Settings → Pages → Build and deployment → Source).

### Moving to Cloudflare Pages and Access later

1. Create a Cloudflare Pages project from this repo. Build command: `python3 scripts/build-site.py`. Output directory: `dist`.
2. Put Cloudflare Access in front of that hostname and allow the owner's email.
3. Make this GitHub repo private as well. Otherwise the JSON stays readable on GitHub even if the site asks for a login.

No dashboard code change is required for that move.

## Safety

Panels only display files already in the public repo. The privacy check and the format check run before anything is published. Paper and research data only: never owner wallet addresses, stake keys, real holdings, balances or real trades.
