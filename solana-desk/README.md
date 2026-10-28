# Solana Paper Desk

A **paper-only** trading desk for Solana tokens. It never holds keys, never signs
transactions, and never touches a wallet: every "trade" is a simulated entry/exit
recorded in a ledger with a fixed 0.5 SOL paper ticket.

Paper fills are marked at the Jupiter quote. That quote already includes its price impact.
Each side then pays a configurable slippage and sandwich-MEV haircut (`desk/config.py`
`COST`, documented there as an assumption: nothing in the Jito sandwich study or the
Helius MEV report is the bps for this ticket) plus the network fee. The default is
**150 bps per side** on a pool of at least 25 SOL, rising toward **300 bps** (the quote's
slippage window) when the pool is thinner or the quoted impact is higher. `trades.csv`
and `paper.json` record gross and net PnL. `pnl` stays the net figure, so older rows
still read. Skip, exit, kill, void, and the v6.5 freeze are unchanged; exits still use
the raw quote price.

- `desk/` — config, skip rules (`skips.py`), books F/R/S (`books.py`), exits, ledger, report.
- `tests/` — unittest suite run against redacted fixtures (third-party holder wallets replaced with placeholders).
- `docs/` — design plan v6.5 (`solana-paper-desk-plan-v6.5.md`; v6.4/v6.5 changes and the Oct 26 config freeze in CHANGELOG.md).
- `CHANGELOG.md`, `findings.md` — build notes and API findings.

## How it runs
Since v6.4 the scanner runs on **GitHub Actions every 30 minutes**
(`.github/workflows/solana-desk-scan.yml`, plus manual runs). It is a plain Python
script (no AI): `python -m desk.run --mode scan --data-dir PATH`. Runs never overlap
(concurrency group). It moved off the Grok Bot box because the box is not awake 24/7
(a heartbeat test showed a ~6h gap). Trade-off: 30-min cadence instead of 10-15 min.

- `--mode scan` (default): checks leads with DexScreener, RugCheck and a Jupiter quote,
  logs `tasks.csv`, updates `state.json` and `health.json`. Never fills.
- `--mode fill`: scan + paper fill step (`desk/fills.py`): marks/exits open positions, then opens fixed 0.5 SOL paper entries in books F, R, S. Writes `trades.csv`, `positions.json` (open positions per book, persisted between runs) and `paper.json` on desk-data. Paper only: no wallet, no signing.
- `--mode auto` (workflow default): fill when `config.FROZEN` and `run.FILL_READY`, else scan. Set the repo variable `SOLANA_DESK_MODE=scan` to pause fills without a PR.

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
