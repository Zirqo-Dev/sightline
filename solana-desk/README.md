# Solana Paper Desk

A **paper-only** trading desk for Solana tokens. It never holds keys, never signs
transactions, and never touches a wallet: every "trade" is a simulated entry/exit
recorded in a ledger with a fixed 0.5 SOL paper ticket.

Paper fills are marked at the Jupiter quote. That quote's `outAmount` already includes the
pool's constant-product move. Each side then pays a slippage and sandwich-MEV haircut
(`desk/config.py` `COST`, an assumption) plus the network fee. The haircut is **60 bps
plus 0.25 times the verified curve impact** of that fill's SOL into the pool's SOL
reserve, **plus 150 bps** on an exit whose reason is `hard_stop`, `trailing_stop` or
`liquidity_pulled`, and it is **capped at 600 bps**. Jupiter's `priceImpactPct` is
logged and is not an input: on the 148–257 SOL pools it read 1.1–2.5% while the curve
and the quoted output are about 0.2–0.34%. New rows are stamped `v3-exit-aware` with
the model's start time. The 7-day and 28-day stats count only those rows. Older rows
keep their numbers and show up labelled `pre-v3`. Skip, exit, kill, void, and the v6.5
freeze are unchanged; exits still use the raw quote price.

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
`{"event_type":"solana-desk-scan"}`. The owner creates that token himself: a separate
fine-grained token for Zirqo-Dev/sightline only (GitHub's docs require Contents read/write
for this endpoint; alternatively call the workflow_dispatch endpoint
`.../actions/workflows/solana-desk-scan.yml/dispatches` with body `{"ref":"main"}`, which
needs Actions read/write). It is stored only in cron-job.org, never in the repo.

Setup: add the repo secret `JUPITER_API_KEY` (free Jupiter key). If it is missing the
scanner falls back to keyless Jupiter and says so in `health.json`.

## Where data goes
All generated files (tasks/trades CSVs, state, daily summaries, `paper.json`,
`log.json`, briefs, review notes, health) are committed to the **`desk-data`** branch
(folder `solana-desk/`) by the Actions bot, with pull-rebase retries and no force-push. `main` holds code only and changes only via PRs the owner merges.

## Rugger list (log only)
RugCheck `rugged` is remembered per deployer in this desk's private Actions cache
(`--cache-dir`, workflow cache `solana-desk-rugger-`, file `rugger-blacklist.json`).
That cache is not the Cardano prefilter cache. A rug recorded on one desk is not on the other.
A later lead from that deployer is appended to `rugger_would_veto.json` on desk-data.
Rugger ids there are `r-` plus 12 hex digits (HMAC-SHA256 under `RUGGER_ID_PEPPER`, any string of at least 32 characters; the 32-byte key is HKDF-SHA256 of that UTF-8 string).
They are not `w-` dev tags. If the pepper is missing, the log keeps the row and omits the id.
The raw list is pushed by a separate sync step after the desk-data commit and before the cache save (`python -m rugger_blacklist.sync`, 2 minute cap). `health.json` `rugger_status.sync` is that step's previous result: `ok`, `unset`, `refused`, or `error`. Only that step sees `SIGHTLINE_PRIVATE_TOKEN` (a fine-grained PAT for `Zirqo-Dev/sightline-private` only, Contents read and write). The sync refuses a repo that is not private. Exclusion hashes live in `solana-desk/rugger.json`, not in the frozen desk config.
This desk does not look up funding hops, so it
does not by itself produce `known_rugger_funded`. The hook stops after 5 seconds inside the hook. Quote pacing and HTTP calls are not part of that budget.
It does not change skip reasons, paper entries, exits, or the timing of quotes and fills. The v6.5 freeze is unchanged.
There is no LP-pull or dump event feed; `lp_unlocked` stays a per-token safety skip.

## Logging only
These fields are records. Skips, book selection, the exit rule, ticket size, and the fill price stay on the frozen v6.5 rules. The fill the book uses is still the Jupiter quote it already requested. A failure in this layer stores null and a short error code and the scan continues.

A failed DexScreener, RugCheck, or Jupiter fetch on a lead writes `fail_reason` on that `tasks.csv` row. The token is `source:code`, and several failures on one lead are joined with `;`. `code` is `429`, `5xx`, `timeout`, `conn`, or `parse`. Any other HTTP status is its number. Anything else is `error`. The exception text is not stored. `health.json` adds `data_fail_by_source` and `data_fail_by_reason` for the UTC day (how many of those fetches failed). `data_failure_rate` and `void_day` stay the share of checks whose skip type is data. Skips, books, exits, and the v6.5 freeze do not read `fail_reason`. A Jupiter 429 or 5xx still gets the one existing retry; a retry that returns a quote is not a failure.

The scan workflow's last step reads that run from `tasks.csv` and `health.json`. It opens one GitHub issue labeled `data-health` when this run's data-failure share is at least 25% of its checks, the same source has a fetch failure on this run and the previous run, or today's data-failure rate was under 0.10 and is now 0.10 or higher. An open issue gets a comment instead of a second issue. Two runs in a row under 5% data failures comment `recovered` and close it. The step is capped at one minute and cannot fail the job. The issue text is counts, reason codes, and the Actions run link. It does not change skips, retries, voiding, entries, exits, or the v6.5 freeze.

No extra HTTP call. Reserves and migration come from the DexScreener pair already loaded for the lead (entry) and for the open-position liquidity check (each later scan and the exit). The quote fields are that same Jupiter quote: the buy for the 0.5 SOL ticket at entry, and the sell for the tokens still held at exit. Scan timing is the local clock.

On the position, and kept on the closed row:

- `entry_sol_reserve`, `entry_token_reserve`, `entry_k` at entry. `k` is `sol_reserve * token_reserve`. `exit_sol_reserve`, `exit_token_reserve`, `exit_k` at close. While the position is open, `last_sol_reserve`, `last_token_reserve`, `last_k` are the latest scan, and `reserve_snaps` keeps up to 48 of those scans. Reserves are DexScreener `liquidity.quote` (SOL) and `liquidity.base` (token). A missing side nulls all three (`reserves_incomplete`) so a partial snapshot is not read as a real `k`. Raw on-chain vault balances are not fetched.
- `pool_created_at` is DexScreener `pairCreatedAt`. `pool_age_at_entry_s` is entry time minus that instant, in seconds. `graduated_pump` is true for dex `pumpswap` and false for any other known dex. `boost_window` is true only when that pool was created on or after 2026-07-21T14:23:00Z and the entry is 0 to 300 seconds later. Unknown dex or unknown time is null. Book F cannot be inside the window: a pass requires the pool to be at least 15 minutes old, and 15 minutes is longer than 300 seconds. There is no separate pump.fun migration API; for a PumpSwap pool, `pairCreatedAt` is the pool creation time used as the migration time. `entry_pool` and `exit_pool` are the DexScreener `pairAddress`. `same_pool` is true when both are known and equal. Each `reserve_snaps` row carries that scan's `pool`.
- `entry_quote_out`, `entry_quote_px`, `entry_quote_impact` and the `exit_quote_*` trio. `*_px` is lamports per raw token, the same units as the fill. `*_impact` is the raw Jupiter `priceImpactPct` field (a fraction, not rescaled; `0.01` means 1%). The trade row's `impact_pct` is that fraction times 100 and is a different cell. The trade row's `quote_px` is unchanged. A missing mark quote stays null (`no_quote`); the desk does not send a second quote. A close whose mark is 0 stores `min_price_held` 0 and `min_price_held_pct` -100.
- `max_price_held` and `min_price_held`, updated on each successful mark, plus `max_price_held_pct` and `min_price_held_pct` versus entry. The exit rule's `peak` is a different field.

`scan_timing.jsonl` in the desk data directory (desk-data `solana-desk/scan_timing.jsonl`) gets one line per completed scan: `scheduled_utc` (the 30-minute cron slot), `started_utc`, `delay_s`, `run_id`, `mode`, and `trigger` (the workflow event name, or `local`). A duplicate slot does not append. Position rows are stamped `obs_v` 1.

## v2 prerequisites (logging only)
`specs/v2-preregistration.md` section 2 is the list. This layer records the blocking gaps G10, G1, G2, G6, and the G5 pool-lookup counts. It does not enter S2, draw the twin, or change books F, R, or S. `specs/tests-log.csv` is the empty G7 log (`date,id,family,metric,result,planned`). `scripts/g8_authorities.py` is the G8 report and does not change the mint/freeze rule. Set `SOLANA_DESK_V2_LOG=0` to skip the writes. A failure in this layer is counted and the scan continues.

Files land in the desk data directory under `v2/` (desk-data `solana-desk/v2/`) and roll on the UTC day and at 5 MB:

- `manifest-YYYYMMDD.jsonl` — one scan header (sources, per-page status and fetch time, pre-filters, `LEADS_PER_RUN`, page limit) and one line per candidate mint. Each looked-up line stores the S2 inputs as fetched. Age and the reserve, k, and death timestamps use that pair's fetch time. Holder rows are percent, excluded, and creator-flag only. Mint and freeze are `revoked` only when the RugCheck key is present and null, and `unknown` when the key is missing. Pass/fail for `graduated_age`, `sol_reserve`, `activity_h1`, `holders`, `k_stable`, and `authorities`. The k series is not copied: the row stores `k_series_n`, the last point, the max consecutive drop, and the pool-snap file stems. A missing k is skipped. A pool-address change starts a new chain. The header lists S2 picks and the twin candidate mints. `twin_draws` is null. G5 is `pool_lookups_failed / pool_lookups_attempted` for this scan's lead lookups only. Follow-up counts stay in `pool_lookups.extra_*`. `rug_fail_rate` is the RugCheck failure share for those leads.
- `pool-snaps-YYYYMMDD.jsonl` — SOL reserve, token reserve, k, pool, and the fetch time. Uncapped. Together with the manifest this is enough to recompute eligibility without another fetch. The position field `reserve_snaps` still keeps 48.
- `death-YYYYMMDD.jsonl` — raw observations from first seen, stamped at the fetch time, plus a label recomputed from those rows. The t0 row's h1 is not part of the zero-swap test. A failed follow is a gap, not a permanent unknown.
- `seen.json` — mint index for the next scan. A file that does not parse is left as it is and that scan skips the v2 log. Not a decision input.

A scan that already fetched a mint makes no extra call for it. Pools that have left the lead page are re-read with DexScreener only, death-window first, at most 24 calls (`SOLANA_DESK_V2_EXTRA_CALLS`, hard max 100), paced at 0.3s, inside a 45s wall clock (`SOLANA_DESK_V2_FOLLOW_BUDGET_S`, hard max 60) and a 5s per-call timeout. The first HTTP 429 stops the follow loop. `follow_skipped_budget` and `follow_stop` are on the manifest header. No extra RugCheck, Jupiter, or RPC call is added to the scan. Set `SOLANA_DESK_V2_LOG=0` to skip the writes and the follow-up reads. A failure in this layer is counted and the scan result is left in place. Typical size is about **1–2 MB/day** across the three logs. The manifest does not copy the k series, so a long history stays in that range. The 5 MB roll is the backstop.

## Tests
    cd solana-desk && python3 -m unittest discover -s tests -t .

## Frozen checks (no decision change)
These read a desk-data `solana-desk` directory (`positions.json`, `tasks.csv`, `trades.csv`). They do not write a book and they do not change a v6.5 entry, exit, or sample.

    python3 specs/day14_verdict.py --data-dir DESK_DATA_DIR --data-commit SHA
    python3 specs/r_overlap_check.py --data-dir DESK_DATA_DIR --data-commit SHA

The day-14 script prints `VERDICT: STOP` or `VERDICT: CONTINUE`. The overlap script does not.
