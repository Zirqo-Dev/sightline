# sol_forensics 0.1

Experimental, research-only look at who bought a Solana mint in its first seconds, where that SOL or stablecoin came from, and which of those wallets look like one actor.

No trading, no signing, no private keys. The labels in a report are uncalibrated hypotheses. They are not trading signals, and the dashboard panel does not show them.

## Layout

Code lives in this package. Data lives next to the Solada desk, because the reports are research for that desk and the dashboard already publishes `solada/`:

| Path | Role |
|---|---|
| `sol_forensics/` | Library and `python3 -m sol_forensics` |
| `solada/forensics/config.json` | Window, budget, watchlist, sourced labels |
| `solada/forensics/reports/<mint>.json` | One report per mint, overwritten each run |
| `solada/forensics/index.jsonl` | One summary line appended per run |
| `solada/forensics/cache/funding.json` | Funder cache checked before any wallet is paged |
| `solada/forensics/panel.json` | Experimental forensics notes for the desk page |

`schema/README.md` describes the desk paper format. Forensics JSON is not that format and is not registered as a paper book.

## Run

From the repo root:

```bash
python3 -m sol_forensics --mint Ceuu7uQHwhtHuMhPWrWL9dgri3FaGJZFkTy6FoDow7NA --holders
python3 -m sol_forensics --watchlist --holders
python3 -m unittest discover -s sol_forensics/tests -v
```

`SOLANA_RPC_URL` is the endpoint when it is set (the GitHub Actions secret). Otherwise the client uses the public mainnet endpoint. The URL is never printed or written into a report. The client allows only `getSignaturesForAddress`, `getTransaction`, `getTokenSupply`, `getTokenLargestAccounts`, `getAccountInfo`, and `getTokenAccountsByOwner`. A configured endpoint stays at or under 8 requests per second. The public mainnet endpoint starts at 2 per second, and after each HTTP 429 or 5xx the gap widens up to one request every 5 seconds. Retries stop at 5. The call budget (default 5000) ends the run with `data_quality.partial = true`.

Useful knobs: `--hops`, `--window-buys`, `--window-seconds`, `--budget`, `--max-pages`, `--wallet-pages`, `--holders`, `--force`.

A wallet whose signature history does not reach the start within `--wallet-pages` (default 5) is `unresolved_busy`. The oldest signature seen on that partial walk is not treated as the funder. Known infrastructure, including the Solada deployer, is labeled and never paged.

## What a report answers

1. Who received the mint in the first 30 buys or the first 30 seconds, whichever reaches further, and how many slots after creation.
2. Where those wallets, and optionally the top holders, were first funded, in SOL or a sourced stablecoin, up to 3 hops.
3. Which of those wallets share a non-exchange, non-infrastructure funder, a direct transfer, or a fee payer, strongly enough to treat as one actor.

Pump.fun mints are walked from the bonding curve first (shorter history, same create transaction). The pump.fun `created_timestamp` is checked against the create transaction's block time. The mint account is paged too when the curve walk finishes, so a transfer that never touches the curve is still visible. If the create transaction is past the page cap, `history_too_deep` is set and early-buyer tracing is skipped.

## Schedule

`.github/workflows/run-forensics.yml` runs on a daily schedule and on `workflow_dispatch` (`mint` input). It reads the `SOLANA_RPC_URL` secret, runs the repo privacy and desk checks, then commits reports with `git pull --rebase` and a normal push. It does not force-push. The schedule does not run the secret smoke test.

Pushes and pull requests for `cursor/sol-forensics-v01-6802` run `python3 -m sol_forensics.smoke_rpc`: one `getSlot` and one `getTokenSupply` for QBITS. That step fails if the secret is empty, and it does not print the URL. `getSlot` stays out of the analysis client. A push of that branch, after the smoke test passes, also reruns the acceptance mints with `--holders` and uploads the reports as artifacts. It does not commit those reports to main.
