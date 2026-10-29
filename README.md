# Sightline

Paper-trading and research desks. Not financial advice. No real positions.

https://zirqo-dev.github.io/sightline/

The site root is the redesigned shell (`index.html` with `preview/app.js` and `preview/app.css`): Home, Cardano, Solana, NFT, and Briefs. It reads `desks.json` and each desk's JSON from the same origin. The previous dashboard stays at `legacy.html`. `preview.html` is the same shell with a preview badge, for the next design pass. Legacy panel layout: [dashboard/PLUGINS.md](dashboard/PLUGINS.md). File format: [schema/README.md](schema/README.md). Privacy: [PRIVACY.md](PRIVACY.md).

The older NFT finder still runs at [nft/site/](nft/site/). After deploy: `https://zirqo-dev.github.io/sightline/nft/site/`. Paste an OpenSea key in that page only. Never commit it.

Experimental Solana early-buyer forensics lives in [sol_forensics/](sol_forensics/README.md). Reports go in `solada/forensics/`. Research only: no trading and no keys. The desk page shows an Experimental forensics panel; the uncalibrated labels are not signals.

## Build

```bash
python3 scripts/build-site.py
python3 -m http.server -d dist
```

The build uses the Python standard library only. It writes `dist/`, generates each desk's `briefs/index.json` from `briefs/YYYY-MM-DD.json`, and includes every file in `dashboard/panels/`. Adding a panel does not require an `index.html` edit: the build injects those script tags into `legacy.html`. Published `index.html` and `preview.html` get a `?v=` query on `preview/app.css` and `preview/app.js`. `legacy.html` gets its own `?v=` on `dashboard/` assets, so a design edit does not retint the previous dashboard.

`node scripts/smoke-dashboard.mjs` checks that the dashboard scripts parse, that P&L stays per unit, that distance-to-kill is computed correctly, that a wallet snippet always renders the same compact tag and color, and that pulse, radar, and data-age behave against `scripts/fixtures/pulse-example.json`. Number checks use `scripts/fixtures/dashboard-smoke.json`. Live desk files only get a shape check, so a bot opening or closing a ticket, or not having written `pulse.json` yet, does not fail the deploy.

Icons live in `dashboard/icons/` and are loaded from the site itself. `python3 tools/resolve-icons.py .` fills icons that are not committed yet. It does not run as part of `scripts/build-site.py`.

## Publish

`.github/workflows/pages.yml` runs on every push to `main`. It runs `scripts/privacy-guard.sh` and `scripts/validate-desks.py` first, then builds `dist/` and deploys with `actions/upload-pages-artifact` and `actions/deploy-pages` only if both checks pass.

The repository Pages source has to be **GitHub Actions** (Settings → Pages → Build and deployment → Source). Until that setting is changed, Pages still deploys the branch itself and will not run this workflow's deploy job.

## Cloudflare later

`dist/` is plain static files and the pages use relative URLs. Point Cloudflare Pages at this repo with build command `python3 scripts/build-site.py` and output directory `dist`, then put Cloudflare Access in front. Make the GitHub repo private too, or the JSON stays readable on GitHub. No frontend change.
