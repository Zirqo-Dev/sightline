# UA-F1

Forward-only paper test. It is an avoid signal around token unlocks: do not hold or buy the token from T-30 through T+0. Research only and paper only. No trading, no keys, no wallets, and no spend. It does not place orders and it does not call a paid API.

The locked preregistration is `prereg_ua_f1.md`, checked by `prereg_ua_f1.sha256`. S0, the lock-time emissions snapshot, is under `snapshots/`. `snapshots/S0/manifest.json` is the lock-time manifest. `snapshots/S0/manifest_addendum1.json` is the later included list (30 protocols, including `helium-network`). The weekly script treats that included list as the universe: an addendum replaces the lock list, and a later addendum or a monthly snapshot replaces the list before it. `solana` stays excluded because it is the L1 coin, not an SPL mint. Nothing under `snapshots/` is rewritten by the runner.

## Schedule

GitHub Actions workflow `.github/workflows/ua-f1.yml`:

- Cron `0 1 * * 1`, Mondays at 01:00 UTC. That run is the weekly pass. It takes a monthly snapshot only when the UTC date is the 1st and this month does not have one yet. After the 1st, if the month has no snapshot, it writes `monthly_snapshot_missing YYYY-MM` to the run log and the status note and does not backfill.
- Cron `0 0 1 * *`, plus retries `23 0 1 * *` and `23 3 1 * *`. Any schedule whose day-of-month field is 1 takes the monthly snapshot and writes its sha256. It does not register episodes or fetch prices. A retry that finds this month's snapshot already taken does nothing.
- Actions tab, Run workflow. The input `mode` defaults to weekly. Choose monthly to take this month's snapshot on any date.

01:00 UTC is the weekly target slot, not a guarantee. GitHub's cron can start late or skip a week. Each weekly run logs the Monday 01:00 slot it is covering and the time it actually started, and it fills any missed week from cache or from the source window. Gaps are logged. They are not interpolated. A monthly run logs its actual start the same way. If that start is after 00:00 UTC on the 1st, the snapshot is labeled with the real start time and the delay is appended to `deviations.md` before any fetch.

The job checks the prereg hash and both snapshot hash files before any fetch. A failed check stops the run.

Counts only until judgment. `python3 ua_f1/ua_f1_weekly.py --judge` refuses until 30 usable episodes have resolved. It does not print a mean, a t statistic, or a win rate before that.

## Kill switch

The paper job has a job-level `if`. Set the repository variable `UA_F1_ENABLED` to `false` to skip the Monday schedule, the 1st-of-month snapshot, and a manual dispatch. Leave the variable unset, or set it to anything else, and the job stays on. The pull-request test job is not part of that switch.

Scheduled workflows on GitHub turn themselves off after 60 days without repository activity. A quiet repo will stop this Monday run until someone triggers it again.

## Where output goes

A scheduled or manual run writes only to the `ua-f1-data` branch (run log, episodes, status counts, price cache, later monthly snapshots). The push step refuses `main`, `master`, and `desk-data`. It does not push to `main`. The same privacy guard the races workflow uses runs on that tree before the push. The guard script itself is not modified here.

Pull requests run `python3 ua_f1/ua_f1_weekly.py --dry-run` via `ua_f1/tests`. That test uses the committed snapshot fixtures, blocks sockets, and writes nothing.

Hosts the runner will call: `defillama-datasets.llama.fi`, GeckoTerminal (`api.geckoterminal.com`), and `data.binance.vision`. `api.llama.fi` is refused. There is no API key.

## S1

S1 is due at 00:00 UTC on 1 December 2026. That day is a Tuesday, so the Monday 01:00 cron would be six days late. The 1st-of-month schedules take S1, label it with the run's actual start, and write `snapshots/S1.sha256` on `ua-f1-data`. The retries at 00:23 and 03:23 UTC do nothing if that snapshot is already there. A weekly run after the 1st does not backfill it. The fallback is the Actions tab, Run workflow, mode monthly.
