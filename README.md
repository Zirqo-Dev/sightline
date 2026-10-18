# Sightline

Paper-trading and research desks. Not financial advice. No real positions.

https://zirqo-dev.github.io/sightline/

The site root is the shared dashboard: a combined Home page, one page per desk, and a Briefs archive. It reads `desks.json` and each desk's JSON from the same origin. Panel layout: [dashboard/PLUGINS.md](dashboard/PLUGINS.md). File format: [schema/README.md](schema/README.md). Privacy: [PRIVACY.md](PRIVACY.md).

The older NFT finder still runs at [nft/site/](nft/site/). After deploy: `https://zirqo-dev.github.io/sightline/nft/site/`. Paste an OpenSea key in that page only. Never commit it.

## Build

```bash
python3 scripts/build-site.py
python3 -m http.server -d dist
```

The build uses the Python standard library only. It writes `dist/`, generates each desk's `briefs/index.json` from `briefs/YYYY-MM-DD.json`, and includes every file in `dashboard/panels/`. Adding a panel does not require an `index.html` edit.

`node scripts/smoke-dashboard.mjs` checks that the dashboard scripts parse, that P&L stays per unit, and that distance-to-kill is computed correctly. Those number checks use `scripts/fixtures/dashboard-smoke.json`. Live desk files only get a shape check, so a bot opening or closing a ticket does not fail the deploy.

## Publish

`.github/workflows/pages.yml` runs on every push to `main`. It runs `scripts/privacy-guard.sh` and `scripts/validate-desks.py` first, then builds `dist/` and deploys with `actions/upload-pages-artifact` and `actions/deploy-pages` only if both checks pass.

The repository Pages source has to be **GitHub Actions** (Settings → Pages → Build and deployment → Source). Until that setting is changed, Pages still deploys the branch itself and will not run this workflow's deploy job.

## Cloudflare later

`dist/` is plain static files and the pages use relative URLs. Point Cloudflare Pages at this repo with build command `python3 scripts/build-site.py` and output directory `dist`, then put Cloudflare Access in front. Make the GitHub repo private too, or the JSON stays readable on GitHub. No frontend change.
