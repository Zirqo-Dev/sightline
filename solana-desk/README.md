# Solana Paper Desk

A **paper-only** trading desk for Solana tokens. It never holds keys, never signs
transactions, and never touches a wallet: every "trade" is a simulated entry/exit
recorded in a ledger with a fixed 0.5 SOL paper ticket.

- `desk/` — config, skip rules (`skips.py`), books F/R/S (`books.py`), exits, ledger, report.
- `tests/` — unittest suite run against redacted fixtures (third-party holder wallets replaced with placeholders).
- `docs/` — design plan v6.3 (v6.4 changes in CHANGELOG.md).
- `CHANGELOG.md`, `findings.md` — build notes and API findings.

## How it runs
Since v6.4 the scanner runs on **GitHub Actions every 30 minutes**
(`.github/workflows/solana-desk-scan.yml`, plus manual runs; the file ships as
`solana-desk/workflow/solana-desk-scan.yml` until it is copied into `.github/workflows/`,
because the bot token cannot write workflow files). It is a plain Python
script (no AI): `python -m desk.run --mode scan --data-dir PATH`. Runs never overlap
(concurrency group). It moved off the Grok Bot box because the box is not awake 24/7
(a heartbeat test showed a ~6h gap). Trade-off: 30-min cadence instead of 10-15 min.

- `--mode scan` (default): checks leads with DexScreener, RugCheck and a Jupiter quote,
  logs `tasks.csv`, updates `state.json` and `health.json`. Never fills.
- `--mode fill`: refused until `config.FROZEN = True` (config freeze at end of day 3).

Runs are idempotent: one scan per 30-min UTC slot, so a duplicate trigger is a no-op.
`health.json` records each run's actual UTC time (`run_times_utc`), `max_gap_min` and the
trigger, so schedule gaps can be measured.

Fallback trigger (if GitHub cron is flaky): cron-job.org can start a run every 30 min with
`POST https://api.github.com/repos/Zirqo-Dev/sightline/dispatches`, headers
`Authorization: Bearer <token>`, `Accept: application/vnd.github+json`, body
`{"event_type":"solana-desk-scan"}`. Bousky creates that token himself: a separate
fine-grained token for Zirqo-Dev/sightline only (GitHub's docs require Contents read/write
for this endpoint; alternatively call the workflow_dispatch endpoint
`.../actions/workflows/solana-desk-scan.yml/dispatches` with body `{"ref":"main"}`, which
needs Actions read/write). It is stored only in cron-job.org, never in the repo.

Setup: add the repo secret `JUPITER_API_KEY` (free Jupiter key). If it is missing the
scanner falls back to keyless Jupiter and says so in `health.json`.

## Where data goes
All generated files (tasks/trades CSVs, state, daily summaries, `paper.json`,
`log.json`, briefs, review notes, health) are committed to the **`desk-data`** branch
(folder `solana-desk/`) by the Actions bot, with pull-rebase retries and no force-push. `main` holds code only and changes only via PRs Bousky merges.

## Tests
    cd solana-desk && python3 -m unittest discover -s tests -t .
