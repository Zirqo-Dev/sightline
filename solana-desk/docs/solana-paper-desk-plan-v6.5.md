**SCAN > VERIFY > EXECUTE > EXIT: v6.5 (config v1-v6.5, frozen Oct 26, 2026)**

> v6.5 = v6.3 thresholds unchanged, plus the v6.4 (GitHub Actions scanner) and v6.5 (data-failure classification fix) changes in CHANGELOG.md. Renamed from solana-paper-desk-plan-v6.3.md; v6.3 history kept below.

_Original title: SCAN > VERIFY > EXECUTE > EXIT: v6.3, day-1 field fixes applied (looser start)_

> **Reviews closed:** the external reviews are done. From now on, changes come only from the owner and Grok Bot.

A paper-only Solana memecoin desk in Sightline's own `solana/` folder, built on the @0xTimQ article. Zero-AI code runs the mechanics plus a time-matched random shadow book; KING does one capped judgment review a day; Snek stays on Solada only; the owner is the only signer. AI does only the daily KING judgment review plus a weekly read. The test is designed to kill a non-edge and can only catch big edges, never prove a small one. Numbers are suggestions or labeled estimates.

> **Honest framing:** the most likely day-28 outcome is KILL or "filters too strict". If that happens, the test worked: paper-only means the downside is time and usage, not money.

## Go gate

| Stage | Status | Condition |
|---|---|---|
| Build (setup runs) | Cleared (reviewer: go) | the owner approves v6.3 (done) and completes his three one-time steps (Jupiter key, sightline-only token, `main` protection) |
| Day 1: threshold and uptime checks | Cleared | Raw API responses reviewed; box loop confirmed unattended or switch to the 30-min Actions fallback before fills |
| Days 2-3: scan + verify only | Cleared | No fills; thresholds may be adjusted on scan pass rates only |
| Day 4: paper fills start | Gated | Only after the config freeze at the end of day 3 (config v1 plus decision rules committed) and all v6 to v6.3 changes in code |

## Baseline: resolved (run both)

> **Decision: run BOTH desks.** Baseline is the Base-only educational paper desk (repo Zirqo-Dev/baseline-desk, Notion backlog with BL-n cards, Claude as analyst and Claude Code as builder). It runs outside Grok, so it adds no Grok usage and does not compete for KING's review slot. Suggestion only (optional): if the owner wants the two desks' results comparable, give Baseline the same structure, a kill rule set in advance plus a random control book.

| Coordination rule | Detail |
|---|---|
| Separate repos | Baseline lives in Zirqo-Dev/baseline-desk; this desk lives in Zirqo-Dev/sightline (`solana/`) |
| Separate mandates | Baseline: Base-only, educational, Claude/Claude Code. This desk: Solana-only, KING + zero-AI code |
| No shared files or tasks | No shared configs, logs, books, backlog cards or credentials; the sightline token never touches baseline-desk |
| Changes | Neither builder edits the other desk; cross-desk ideas go to the owner, not to the other repo |

## What changed in v6.3 (day-1 field fixes, looser start; approved by the owner)

Based on the day-1 API check (one snapshot of 30 Solana tokens from the paid feed; indicative only).

| # | Change | Why (day-1 data) |
|---|---|---|
| 1 | Liquidity floor is now **10 SOL on the pool's SOL side**, replacing $50k USD | DexScreener's USD figure counts the token side; one pool showed $83k with only 6.3 SOL |
| 2 | Ticket-to-pool cap loosened from 1% to **5% of the pool's SOL side** (0.5 SOL vs 10 SOL) | Matches the new floor; the 3% sell-side price-impact check stays as the real slippage guard |
| 3 | LP lock by pool type: Meteora DLMM = not applicable; PumpSwap = no signal (always 100%); other pools keep 90% | DLMM always reports 0%, PumpSwap always 100% |
| 4 | Top-10 holders exclude pool, bonding-curve and locker accounts | Raw top-10 82.5% became 28.7% after exclusion on one token |
| 5 | Risk score limit 40 applies to RugCheck `score_normalised` (0-100, higher = worse) | Raw `score` is unbounded (1 to 17,425 seen) |
| 6 | Holder data comes from RugCheck, not the public RPC | Public RPC holder call returned 429 twice |
| 7 | Ungraduated (bonding-curve) tokens are out of scope: safety skip `not_graduated` | 16/30 feed tokens were still on a bonding curve |

Snapshot pass count: of 14 graduated tokens, **9 had at least 10 SOL** on the SOL side (7 at 25 SOL). This is a looser start: tighten later only on data, as a logged pass-rate change before the freeze.

## What changed in v6.2 (approved by the owner)

| # | Change | Why |
|---|---|---|
| 1 | Day 14 is only an early STOP for clear failure: at least 20 F closes AND F expectancy after costs worse than -15% per trade; otherwise continue | One real test instead of two cuts the chance of killing a real edge by accident |
| 2 | Day 28 is the one real test: KILL if F after costs doesn't beat R; PASS if F beats R AND F expectancy is above 0; under 20 F closes = STOP, "filters too strict" | Simpler rules that are hard to argue with afterwards |
| 3 | The top-5%-trimmed comparison becomes a report warning, not a kill clause; the bootstrap bound is reported, not a kill clause; second-pass kill checks removed | Fewer clauses means fewer false kills |
| 4 | A daily no-AI health line (loop ran, check count, error rate, data-failure rate) on the dashboard | Mechanics stay zero-AI and visible at a glance |
| 5 | AI does only the daily KING judgment review plus a weekly read | Zero-AI mechanics confirmed |
| 6 | External reviews are done; from now on it's the owner and Grok Bot only | Stops the plan from moving |

## What changed in v6.1 (wiring fix)

| # | Change | Why (review) |
|---|---|---|
| 1 | Every generated file goes on `desk-data`: daily summary, `paper.json`, `log.json`, `briefs/`, `review.json` (plus tasks/trades/state) | Protecting `main` would otherwise block the desk's own daily writes, tempting someone to switch protection off |
| 2 | `pages.yml` checks out `main` (code) plus `desk-data` (data) and builds the site | Dashboard still updates without any write to `main` |
| 3 | `main` changes only via PRs the owner merges; the box token never needs write access to `main` | Branch protection can stay on permanently |
| 4 | The setup run opens a PR instead of pushing; the owner merges it from his phone | Same rule applies from day 0 |
| 5 | New Checkpoints section (end of day 3, day 7, days 14/28) | Pre-commits what to do when tempted |
| 6 | Optional: the owner logs his own KILL/STOP/PASS prediction with a confidence before day 4 | Calibration check, whatever the desk does |
| 7 | Honest framing line: KILL or "filters too strict" is the most likely day-28 outcome | That outcome means the test worked |

## What changed in v6 (final review round)

| # | Change | Why (review) |
|---|---|---|
| 1 | Minimum sample: KILL needs at least 20 F closes; under 20 at day 14 = auto-extend; under 20 at day 28 = stop, "filters too strict to trade" (day-14 part superseded in v6.2) | With stacked filters, F could have under 10 closes and the test becomes a coin flip |
| 2 | Config freeze moves to end of day 3; adjustments allowed only on scan pass rates, never prices | Days 2-3 are the first look at pass rates; no fills yet, so nothing to overfit |
| 3 | Trim the same share from each book (top 5%), not top 2 each | R can be 2x F, so a fixed count strips more from F |
| 4 | Fixed 0.5 SOL paper ticket in every book; halts logged as "would have halted", books never stopped | Balance-based sizing drifts per book; an F halt would also stop R |
| 5 | Mid-test infra switch starts a new book; loop outage over 2h voids new entries for that window | A cadence change is a strategy change |
| 6 | False-kill rates restated (reviewer's estimate): about 1/2 for a real +5% edge, about 15% for +20% (superseded in v6.2) | Three kill clauses checked twice kill more often; the test catches big edges only |
| 7 | PASS also needs F's own expectancy after costs above zero | Beating random while losing isn't income |
| + | Branch protection on `main` (optional, adopted): the box token can write only to `desk-data`; the owner sets the rule himself on github.com | Limits damage if the token leaks |

Earlier rounds already added: the hypothesis and pre-set rules, F/R/S shadow books with identical execution checks, stale-aware exits, data/safety/execution skip split, bootstrap expectancy, time-matched R, void days, no X-read metric, Option A infra with a sightline-only token, the day-1 threshold re-check, and the Baseline decision.

## Why Cardano was dropped

> **v2 (Cardano) is retired.** Cardano pools are thin, and Snek never got a single ticket through its own k and y minimums (holders and pool depth), so a Cardano paper test would mostly log skips. The review's fair counterpoint: zero passes at a safety bar is itself a result, and moving to Solana adds sample size, not quality. That is exactly why the plan adds a random control book.

## Hypothesis and decision rules (frozen end of day 3)

**Hypothesis:** On Solana tokens from the DexScreener paid feed (profiles and boosts), aged 15 min to 48 h, the safety filters plus the exit ladder beat random entry from the same feed and the same runs, after modeled costs, with identical execution checks and a fixed 0.5 SOL ticket in every book.

**What the test can and cannot do:** with roughly 20-60 F closes, per-trade returns are so dispersed (the review's illustration: a 60% standard deviation gives about ±21 points of 95% interval at 30 closes; a true +5% edge needs on the order of 500+ closes) that the test **can only kill an edge, never prove one**, and it only reliably catches big edges.

**False kills:** the earlier reviewer estimate (about 1/2 for a real +5% edge under the old three-clause, two-checkpoint rule) no longer applies. The simpler rule (one real test at day 28 plus a day-14 stop only for clear failure) lowers false kills. I have not computed new rates. **KILL means "edge not shown", not "proven dead".**

**Measures:** mean per-trade return (% of the 0.5 SOL ticket) after costs for F and R, including open positions at mark (stale marks flagged) and confirmed rugs at -100%.

**Report warnings (never kill clauses):** top-5%-trimmed F vs trimmed R (same share per book, at least 1 trade); bootstrap 95% interval of F's expectancy. A warning means "check whether one big winner drives the result"; it never changes the decision.

| Step | Condition | Outcome |
|---|---|---|
| Day 14 | At least 20 F closes AND F expectancy after costs worse than -15% per trade | Early STOP ("clear failure") |
| Day 14 | Anything else | Continue to day 28 (no other test) |
| Day 28 | F has fewer than 20 closes | STOP: "filters too strict" (a result, like Cardano's) |
| Day 28 | F expectancy after costs does not beat R's | KILL ("edge not shown") |
| Day 28 | F beats R AND F expectancy after costs is above 0 | PASS |
| Day 28 | F beats R but F expectancy is 0 or below | KILL ("beats random but loses money") |
| PASS | Paper-only continuation until 200 F closes or 12 weeks after day 1, whichever comes first | Same day-28 rule applied once at the end; never live |

| Standing rule | Detail |
|---|---|
| Live | Out of scope. Any live step is a separate written decision by the owner, with its own wallet and cap |
| Config | Adjustable on days 1-3 on scan pass rates only (never prices); frozen at the end of day 3. Any later change starts a new book; books are never pooled |
| Infra switch | A mid-test switch (box to Actions or back) starts a new book |
| Loop outage | Over 2 hours: new entries in that window are voided; open positions keep being tracked |
| Data failures | Above 10% in a day: that day's new entries voided in all books |

## Recommendation

> **A separate `solana/` desk with a zero-AI loop plus a time-matched random shadow book, and one unscored KING review a day. No new bot.** Code runs SCAN, the hard-skip VERIFY, paper EXECUTE and EXIT for all books. KING's "Solana paper desk" routine reviews at most 3 survivors a day as a judgment pass (no score, no metric). Day 14 is an early stop for clear failure only; day 28 is the one real test (decision table above). A dedicated bot stays a later option only if a much longer paper test passes.

## Mandate separation (decided by the owner)

| Agent / thing | Mandate | Does not |
|---|---|---|
| Snek Sentinel | Solada only: `solada/` (incl. its existing Pump.fun comparison book), score, rules, nightly brief | No trading-desk role; never reads or writes `solana/` |
| KING: 'Solana paper desk' routine | 1 daily unscored judgment review of at most 3 survivors; wallet/cluster forensics notes; report | Never edits `config.py`; no scored X-read metric; never mixes with NFT desk logs |
| KING: NFT desk | Unchanged (`nft/`) | - |
| Zero-AI loop (code) | SCAN, hard skips, all books' paper fills and exits, `tasks.csv`/`trades.csv` | No AI |
| the owner | Edits `config.py`, approves changes, only signer | - |
| Shared | Only the gathering pattern (pulse.sh style, exit codes, privacy guard, validator) and helper code | Never mandates, rules, books or logs |

Why: one agent with two desks blends rules and leaks lessons across books. A separate routine, folder and logs gives every trade a single owner (the article's "nobody holds two"). KING fits because of its wallet/cluster forensics; the review notes it was also chosen partly because its routine was paused (availability, not only fit). That is acceptable because KING's role is now small and unscored.

## Source article

@0xTimQ, "SCAN. VERIFY. EXECUTE. EXIT. BUILD A GROK BOT TRADING DESK WHERE NOBODY SKIPS A STEP", X Article, Oct 20, 2026 (https://x.com/0xTimQ/status/2112584406988677351). Retrieved in full. Its promo posts' returns are unverified marketing, and every threshold below comes from a promo article, so day 1 re-checks them.

> "FACTS ARE CODE. a model never decides if liquidity is locked. MISSING IS A SKIP." (@0xTimQ)

| Article piece | Kept as | Change |
|---|---|---|
| config.py (bot never edits) | `solana/desk/config.py` | Values below; frozen for the test window |
| verify.py hard_skip + Grok X read | Hard skips in code | X read removed from the scored path; KING's daily review is unscored |
| book.size: size off balance (10%), max 1% of pool | Fixed 0.5 SOL ticket in every book; ticket cap (5% of pool SOL side in v6.3) kept as an execution check | Balance-based sizing would drift per book and bias the comparison |
| exits.py ladder/trail/time/liq | Same logic, all books | Stale-aware exit marking; time stop 120 min (cadence) |
| tasks.csv / trades.csv / state.json | Same files, on the `desk-data` branch | Batched commits, not one per run |
| report.py | Same, plus F vs R, trimmed and bootstrap warnings, and the daily health line | KING reads it, doesn't recompute |
| 5-min routines | 10-30 min loop (see infrastructure) | Not on a 5-min Actions cron |
| xAI API key in .env | Dropped | No extra bill |

## Shadow books (zero AI)

| Book | What gets a paper fill | Purpose |
|---|---|---|
| F: filtered | Leads that pass every safety filter AND the execution checks | The desk |
| R: random | Random sample of leads from the same runs where F fills, about F's daily count, capped at 2x F; must pass the execution checks; safety filters ignored; seeded and logged | Control: does filtering beat luck and the regime? |
| S: rule-skipped | Tokens failing a safety filter but passing the execution checks, same cap as R | Do the safety skips actually avoid losers? |
| U: unpaid feed | Deferred to a later run, with the same age and liquidity windows as F | Will test paid-feed adverse selection without confounding token mix |

**Execution checks (all books):** a Jupiter route exists, sell-side impact at size at most 3%, ticket at most 5% of the pool's SOL side, at least 1 sell in the last hour.
**Safety filters (F only; S = fails these):** not graduated (bonding curve), 10 SOL SOL-side floor, LP lock by pool type, mint/freeze authority, dev and top-10 concentration, RugCheck risk score, age window, rugged/danger flags.

All books use the same fixed 0.5 SOL ticket, costs and exits, with their own paper bankroll; shadow books never block F's slots. Halts are logged as "would have halted" and never stop a book.

## Infrastructure and cadence

| Option | How | Pros | Cons |
|---|---|---|---|
| A. Loop on the Grok Bot cloud computer (recommended) | Plain Python scheduled on the box every 10-15 min, zero AI; pushes to the `desk-data` branch at most hourly; Actions only deploys Pages a few times a day | Actions is used only for publishing, which is what it's for | Box uptime for unattended schedules is unverified (day-1 check); the push credential is the main risk (see token) |
| B. GitHub Actions every 30 min (fallback) | cron `*/30`, concurrency group, batched commits | Free; scheduled data collection has precedent (GitHub OCTO's Flat Data fetches data on a schedule and commits it); reviewer rates 48 short runs/day as low risk | Still a grey zone under the Actions terms; a flag would affect the whole Zirqo-Dev account |
| Rejected: Actions every 5 min | 288 runs/day | Matches the article | Highest terms risk; exceeds the Pages soft limit of 10 builds/hour |
| Rejected: new GitHub account or Enterprise | - | - | One free personal account per person; Enterprise has the same Actions terms plus a bill |

**Push credential (Option A):** a fine-grained personal access token that the owner creates himself, scoped to Zirqo-Dev/sightline only, permission Contents: read and write (nothing else), expiry 30-60 days. Stored as a box secret, never pasted in chat, never all-repo access, rotated at expiry.

**Branch protection (adopted):** the owner adds a rule on github.com protecting `main` (require pull requests, no direct pushes), so the box token never needs write access to `main` and writes only to `desk-data`. Code changes to `main` arrive only as PRs the owner merges (from his phone if he likes), including the setup run's PR.

**Infra switch rule:** switching between Option A and B mid-test starts a new book; a loop outage over 2 hours voids new entries for that window.

Either way:
- **Concurrency group** (`concurrency: solana-desk`, cancel-in-progress false) or a lock file on the box, so overlapping runs can't race on state.json.
- **Data branch** `desk-data` for tasks/trades/state; `main` holds only code; every generated file (daily summary, `paper.json`, `log.json`, `briefs/`, `review.json`) lives on `desk-data` too.
- **Pages deployed via an Actions workflow** (custom deploys don't count toward the 10 builds/hour soft limit), a few times a day, never per loop.
- **Jupiter free API key** as a secret (keyless api.jup.ag is about 0.5 requests/second and lite-api is being throttled toward shutdown, per the review; key terms unverified by me).

## What cadence means for fills

1. Checks every 10-30 min (plus lateness) mean exits are sampled, not managed. Late checks cut both ways: stops can fill better after a bounce and ladder takes can fill higher, but rugs only cut one way.
2. Entries fill at a fresh Jupiter quote in the same run, not the scan price.
3. **Temporary errors (timeout, rate limit, API error):** the position stays open at its last mark, flagged `stale`, and is retried next run. The stale-mark count is reported daily.
4. **Confirmed failure:** -100% only when the pool is confirmed gone, or there is no route for 3 consecutive checks. Otherwise exits fill at the quote when checked (gap-through).
5. Open positions are reported at current mark; confirmed rugs count at -100% in expectancy.
6. Time stop 120 min (about 4-12 checks) instead of 60, so the exit isn't decided by one or two samples.
7. Unmodeled optimism remains: failed transactions, landing latency, priority fees/tips, competition for the same quote. The v6.5 freeze shipped a flat 0.5%/side haircut as a guess. PnL accounting now uses `COST` in `config.py`: 60 bps per side plus 0.25 times the verified constant-product impact, plus 150 bps on hard_stop, trailing_stop and liquidity_pulled, capped at 600 bps. Jupiter `priceImpactPct` is not that impact. Exits, kill lines, and the freeze are unchanged. Paper results are a ceiling on live results.

## Data sources (checked Oct 24, 2026, unauthenticated, from the box)

| Source | Endpoint | Use | Status |
|---|---|---|---|
| DexScreener | `api.dexscreener.com/token-profiles/latest/v1`, `/token-boosts/latest/v1`, `/latest/dex/tokens/{mint}` | SCAN leads (paid feed), pair liquidity, age, txns | Verified 200, no key; boosts are multi-chain, filter chainId=solana; third-party docs cite 60 req/min (profiles/boosts) and 300 (pairs), unverified |
| Jupiter quote | `api.jup.ag/swap/v1/quote` with free key (lite-api being retired) | Paper fills with price impact, both sides | Verified 200 keyless today; free key terms unverified |
| RugCheck | `api.rugcheck.xyz/v1/tokens/{mint}/report/summary` (full: `/report`) | Mint/freeze authority, risks, score, lpLockedPct | Verified 200, no key; score direction and top-holder fields re-checked on day 1 |
| GeckoTerminal | new_pools endpoint (Solana) | Unpaid lead feed for book U (deferred) | Unverified |
| Solana public RPC | `api.mainnet-beta.solana.com` | Slot/health only (holder data now from RugCheck) | getSlot 200; getTokenLargestAccounts 429 on day 1 |
| Helius free tier | RPC/enhanced tx | Better holder/bundle data | Needs an API key; terms unverified; only if public RPC fails |

## Day-1 threshold re-check (done, SPD-1)

Done on Oct 24, 2026 from the box (findings.md, raw responses in raw/). Jupiter lite-api still answers; DexScreener and RugCheck need no key. Results are applied as the v6.3 field fixes above. Thresholds may still be adjusted on days 2-3 on scan pass rates only (never prices), as logged changes; config v1 is frozen at the end of day 3, before any paper fill.

## config.py starting values (v6.5, frozen Oct 26, 2026; values unchanged since v6.3)

| Setting | Value |
|---|---|
| PAPER | True, always |
| START_BALANCE | 10 SOL paper bankroll per book (F, R, S); F at most 4 open x 0.5 SOL = 2 SOL, R and S at most about 8 open = 4 SOL, so the bankroll never limits a fill |
| LEADS_PER_RUN | 12 (article) |
| min_quote_liquidity_sol | 10 SOL on the pool's SOL side (v6.3; replaces the article's $50k USD) |
| not graduated | Bonding-curve tokens (pumpfun, meteoradbc) = safety skip `not_graduated` |
| min_lp_locked_pct | 90 (article) on DAMM/Orca/Raydium; DLMM not applicable; PumpSwap no signal (always 100%) |
| max_dev_pct / max_top10_pct | 10 / 35 (article); top-10 excludes pool, curve and locker accounts; holder data from RugCheck |
| max_risk_score | 40 on RugCheck `score_normalised` (higher = worse) |
| age | 15 min to 48h (article) |
| mint/freeze authority | Open authority = skip (article) |
| max_ticket_share | 5% of the pool's SOL side (v6.3; article had 1% of pool); execution check, all books |
| Execution checks, all books (suggested) | Jupiter route exists; sell-quote impact at size at most 3%; at least 1 sell in the last hour (article) |
| TICKET_SOL | Fixed 0.5 SOL per entry, every book, for the whole test (replaces the article's SIZE_PCT); returns reported as % of ticket and in SOL |
| MAX_OPEN | 4 for F (article); R and S capped by R_MATCH |
| SESSION_LOSS_HALT_PCT | 30 (article) on the 10 SOL bankroll, logged as "would have halted" only, never stops a book; no more than 8 new F entries/day |
| EXIT | hard_stop -25; ladder (+40, 1/3), (+100, 1/3); trail 25% after the first take; liq_drop 40; time stop 120 min unless up 10%; temporary error = stale + retry; pool confirmed gone or no route 3 checks in a row = -100% |
| Modeled costs | Jupiter `outAmount` both sides (the curve is already in the quote), plus a slippage+MEV haircut on each side, plus the network fee. The frozen table's 0.5%/side guess is the legacy entry cost for rows opened before `entry_cost` was stored. Live default: `COST` in config.py (`v3-exit-aware`: 60 + 0.25 × curve impact bps, +150 bps on hard_stop / trailing_stop / liquidity_pulled, cap 600). PnL accounting only |
| R_MATCH | R drawn from the same runs as F fills, about F daily count, cap 2x F |
| RANDOM_SEED | Logged per run; R sample is reproducible |
| TRIM_PCT | Top 5% of trades by return removed per book; report warning only |
| MIN_F_CLOSES | 20 (needed for the day-14 early stop and for any day-28 KILL/PASS) |
| EARLY_STOP_EXP | -15% per trade after costs (day-14 early stop, with at least 20 F closes) |
| OUTAGE_VOID_H | 2 (loop outage over 2h voids new entries in that window) |
| DATA_FAIL_VOID | Above 10% data failures in a day: void that day's new entries in all books; keep tracking open positions |

## Files and rules

| Path | Purpose |
|---|---|
| `solana/desk/config.py` | Every number; the owner edits, bots never do; frozen end of day 3 |
| `solana/desk/scan.py, verify.py, desk.py, book.py, exits.py, report.py, quote.py, shadow.py` | Article's files plus the shadow books (F, R, S) |
| `desk-data` branch: `tasks.csv, trades.csv, state.json` | Logs and state (7-day window + monthly archive) |
| `desk-data` branch: `solana/health.json` | Daily no-AI health line: loop ran (yes/no, runs vs expected), check count, error rate, data-failure rate; shown on the dashboard |
| `desk-data` branch: daily summary | Daily report (generated) |
| `.github/workflows/solana-desk.yml` | Option B loop (30 min) or a light sync job; concurrency group |
| `.github/workflows/pages.yml` | Checks out `main` (code) plus `desk-data` (data), builds and deploys the site via Actions, a few times a day |
| `desk-data` branch: `solana/paper.json, log.json, briefs/` | Schema-v2 mirror for the dashboard, generated daily |
| `desk-data` branch: `solana/review.json` | KING's unscored daily review notes |
| `desks.json` entry 'solana' | Bot: KING (Solana paper desk routine) |

Rules:

1. Paper only. No wallets, keys, signing or swaps; the owner signs everything, ever.
2. Never show the owner wallets or holdings; other wallets as stable short tags (w-7f3a); privacy-guard on every push.
3. For entries, missing data is a skip and a skip is final (article). For exits, a temporary error means stale + retry; only a confirmed gone pool or 3 no-route checks means -100%.
4. Execution checks apply to every book; only safety filters differ.
5. Bots never edit config.py; days 1-3 adjustments on scan pass rates only; frozen end of day 3; any later change, or an infra switch, starts a new book.
6. Halts are logged as "would have halted"; they never stop a book.
7. Snek never touches `solana/`; KING's desk routine never touches `solada/` or `nft/`.
8. The decision rules above are frozen with the config at the end of day 3.
9. `main` is branch-protected; all generated files and the box token's writes go only to `desk-data`; `main` changes only via PRs the owner merges.

## Logging: tasks.csv / trades.csv

| File | Columns |
|---|---|
| tasks.csv | time, run_id, agent (SCAN-n/VERIFY/EXECUTE/EXIT/REVIEW), task, mint, book (F/R/S), result (lead/skip/go/paper_fill/exit), skip_type (data/safety/execution), reason, source (profile/boost/manual), check_delay_s, cfg_version, seed |
| Data-skip reasons | missing_data, api_error, timeout, no_route, rate_limited |
| Safety-skip reasons | rugged, not_graduated, authority_open, danger:*, thin_pool, lp_unlocked, dev_holds, top10_heavy, risk_score, too_fresh, too_old |
| Execution-skip reasons (all books) | no_route, impact, ticket_share, nobody_can_sell, max_open, void_day, void_outage |
| Halt log | would_halt (timestamp, book, drawdown) recorded; no entries are blocked |
| trades.csv | time, book, agent, mint, symbol, action (buy/sell/mark), frac, sol (0.5 per entry), quote_px, impact_pct, fee_sol, haircut_pct, why (take_profit_40, take_profit_100, hard_stop, trailing_stop, time_stop, liquidity_pulled, pool_gone, no_route_3x), pnl (net SOL), check_delay_s, stale (0/1), then gross_pnl, net_pnl, entry_haircut_pct, exit_haircut_pct, verified_impact_bps, cost_model, cost_model_started_at. Older files whose header is a prefix of this list keep their pnl and get blank cells for the new columns. Blank cost_model is labelled pre-v3. 7-day and 28-day stats count only v3-exit-aware |
| state.json | per book: cash, open{mint: entry, peak, steps, liq0, opened, last_mark, stale, no_route_count}, session start |
| review.json (KING) | date, mints[at most 3], notes (forensics, bundle/sniper/cluster tags), no score |

## Cost: offload ladder and estimates

1. **Free APIs + code first (0 AI):** DexScreener, RugCheck, Jupiter (keyed). Cache RugCheck per mint for 30 min.
2. **Safety filters, execution checks and all books in code (0 AI):** Most leads die here.
3. **Fills, exits, report, bootstrap in code (0 AI):** Silent when nothing happens.
4. **One unscored KING review/day on at most 3 survivors:** Reads report + digest only, low effort; no X-read scoring.
5. **Hard caps:** 1 routine run/day, 0 reruns, digest at most 150 lines; one weekly read. That is the only AI in the desk.

| Mode | Runs/week | Est. % of weekly pool |
|---|---|---|
| Setup (one-off, incl. day-1 re-check) | 3-4 | 10-22% |
| Lean desk (recommended) | 7 + 1 weekly | 12-30% |
| Extra-lean (review every other day) | 4 | 7-18% |
| Code-only (no KING review) | 1 weekly | 4-8% |

Estimates back-solved from the pool hitting 82% mid-week with KING at about 3 runs/day plus Snek (about 4-6% per full run); token counts aren't published. Dropping the X read makes the daily pass smaller than in v3. Snek's Solada usage is separate and on top; Baseline runs outside Grok and adds nothing. Check usage on day 3 and day 7.

## Cloud-only setup

| Piece | Runs on | Part for the owner |
|---|---|---|
| Desk loop | Grok Bot cloud computer (Option A), Actions every 30 min as fallback (Option B) | None |
| Daily review | KING routine 'Solana paper desk' on the Grok Bot cloud computer | Read the report on phone |
| Dashboard | zirqo-dev.github.io/sightline, Solana desk tab with the daily no-AI health line at the top, deployed via Actions | Phone browser |
| Docs | Notion (connected) | - |
| Keys | Free Jupiter API key; fine-grained sightline-only token (Contents read/write, 30-60 day expiry) | the owner creates both himself; stored as secrets, never in chat |
| Local machine | Nothing: no server, Docker or CLI | - |

## Paper test (28 days with a day-14 early stop, PASS capped at 200 F closes or 12 weeks)

1. **Setup (3-4 KING runs):** Port the article's files plus shadow.py (F, R, S) with the fixed 0.5 SOL ticket; Option A with lock file, `desk-data` branch, Pages via Actions; Jupiter key and sightline-only token as secrets; the owner protects `main`; the setup run opens a PR that the owner merges from his phone.
2. **Day 1: threshold re-check and uptime check:** Raw responses, LP/top-10/RugCheck checks, confirm the box loop runs unattended (else switch to Option B now, before any book exists).
3. **Days 2-3: scan + verify only:** No fills. Check-in 1: pass rates per filter, data-failure rate, safety vs execution skip mix, check delays. Adjust thresholds on pass rates only, then freeze config v1 and the decision rules at the end of day 3.
4. **Days 4-14: F, R, S live, config frozen:** Daily unscored KING review. Check-in 2 (day 7): F close count vs the 20 minimum, voided windows, R/F ratio, stale marks, usage %.
5. **Day 14 and day 28: apply the decision table:** Day 14 is an early STOP only for clear failure. Day 28 gives STOP ("filters too strict"), KILL, or PASS (F beats R and F expectancy above zero).

## Checkpoints

| When | What to do |
|---|---|
| End of day 3 | If projected F closes by day 14 come out well under 20, accept it. Don't loosen thresholds just to get trades; only pass-rate fixes for broken fields count. |
| Daily | Glance at the no-AI health line: loop ran, check count, error and data-failure rate. |
| Day 7 | Check the F close count against 20, the data-failure rate, and whether stale marks are clearing. |
| Day 14 | Early STOP only if at least 20 F closes and F expectancy after costs is worse than -15% per trade; otherwise continue. |
| Day 28 | Apply the decision table as written, even if a single big winner makes it tempting not to (the trimmed warning is there to flag exactly that). |

**Optional:** before day 4, record your own prediction in your prediction log: KILL, STOP or PASS, with a confidence. At day 28 you'll learn something about your own calibration, whatever the desk does.

## Biggest risks

| Risk | Honest read | Mitigation |
|---|---|---|
| No real edge | Memecoin profit needs a later buyer paying more; a bot seeing tokens late with common rug filters has no stated reason to be early | The kill rule vs a random book is built to expose this quickly |
| Adversely selected feed | Paid boosts/profiles are bought by teams seeking buyers | Book U in a later run, same age/liquidity windows |
| Lottery payoff | Mostly -25% to -100% with a few big wins; not a "small daily harvest" | Top-5%-trimmed report warning; PASS needs F expectancy above zero |
| Most tokens go to zero | The base rate | Skip default, small size, time stop |
| Rugs / LP pulls | Faster than any 10-30 min check | -100% on confirmed gone pool or 3 no-route checks; rugs in expectancy |
| Bundles and snipers | Bundled supply passes top-10 checks | Curve/pool exclusion; KING notes (unscored) |
| MEV, failed tx, latency | Live fills worse than quotes; the exact bps is unmeasured | `COST` haircut assumption (v3-exit-aware: 60 + 0.25 × curve bps, +150 on stress exits, cap 600) on entry and exit; paper treated as a ceiling |
| Noise / overfitting | 20-60 closes can't confirm anything; real edges can still be killed by chance | One real test at day 28 (lowers false kills vs v6); KILL = not shown; min 20 closes; config frozen end of day 3 |
| Filters too strict | Stacked safety filters may produce under 20 F closes | Day-14 early stop needs 20 closes; STOP "filters too strict" at day 28 |
| Infra change mid-test | Cadence change is a strategy change | Switch = new book; outage over 2h voids entries |
| Biased control | R paying worse execution than F would fake a PASS | Execution checks on all books; R time-matched |
| Credential leak | Push token on an AI agent's machine | Fine-grained, sightline-only, Contents only, 30-60 day expiry; `main` branch-protected |
| Regime | Two weeks covers one memecoin regime | Pass means more paper, across a longer window |
| Infra / terms | Actions misuse flag could hit all Zirqo-Dev repos | Option A; 30-min Actions fallback (Flat Data precedent, low risk) |
| Silent API failure | Throttled quotes look like skips | Jupiter key; data-failure void above 10% |
| State races | Overlapping runs create phantom positions | Concurrency group / lock file; data branch |
| Moving target | Keeper bot, Cardano, Baseline, Solana in about a month (review) | Freeze v6.3 for the full test; external reviews are closed; Baseline runs separately with no shared files or tasks |
| Usage burn | Past 82% mid-week | Zero-AI mechanics, caps, usage check-ins |

## What success looks like in the logs

| Metric | Healthy | Red flag |
|---|---|---|
| Daily health line | Loop ran, check count near expected, low error and data-failure rate | Missed runs or rising errors |
| F minus R expectancy after costs (day 28) | Above zero | Zero or negative: KILL |
| F close count | At least 20 by day 14 | Under 20 at day 28: STOP "filters too strict" |
| Trimmed F vs trimmed R (top 5% removed each) | Trimmed F above trimmed R | At or below: report warning (not a kill) |
| F expectancy after costs | Above zero at day 28 | Zero or below: no PASS |
| Expectancy incl. open-at-mark and confirmed rugs | Reported daily alongside closed-only | Closed-only looks good, all-in doesn't |
| Stale-mark count | Low and clearing within a few runs | Persistently high: data problem |
| Data-failure rate | Below 10% per day | Above 10%: that day's new entries voided in all books |
| R/F daily count ratio | About 1x, never above 2x | R far above F |
| S book vs R | S does worse than R | S as good or better: safety skips don't help |
| Exit slippage beyond stop | Distribution reported per book | Heavy tail ignored |
| Check delay | Median and p90 reported | p90 near or above the time stop |
| Max drawdown (all-in, F) | Under 20% of the 10 SOL bankroll | "Would have halted" logged |
| Voided windows | Few outage/void-day windows | Frequent voids: infra problem |
| Cost | At most 1 KING run/day plus a weekly read, under about 30%/week | Reruns or cap overrides |

Removed earlier because they pass on a broken desk: skip rate of 90% or more, average win at least 2x average loss, losses clustering near -25%, and the KING X-read metric.

## Options

| Option | Verdict |
|---|---|
| A. Article literally (5-min agent routines, xAI key) | Reject: usage, separate bill, terms risk |
| B. Give the desk to Snek | Reject: muddles the Solada mandate (call made by the owner) |
| C. `solana/` desk: zero-AI loop (Option A) + F/R/S books + unscored daily KING review | Recommended |
| D. Same, KING review every other day or code-only | Fallback if usage runs hot |
| E. New dedicated bot | Only after a much longer paper test passes |

**Pros**
- Has a control group and a pre-set kill rule
- Zero-AI mechanics; AI spend independent of lead volume
- Clean mandates, one owner per step

**Cons**
- Can only kill, not prove, an edge in 2-4 weeks
- Sampled exits keep paper results optimistic
- Box uptime (Option A) and several free API terms unverified
- Can still kill a real small edge by chance; catches big edges best

## Minimal viable next step

1. **the owner approves v6.3 (done):** Baseline keeps running in its own repo; Snek's Solada routine unchanged; nothing unpaused yet.
2. **the owner does three one-time steps himself on his phone or laptop:** The free Jupiter API key; a fine-grained token for Zirqo-Dev/sightline only (Contents read/write, 30-60 day expiry); a branch protection rule on `main`. Keys go in as secrets, never into chat.
3. **One KING setup run:** Builds `solana/desk/` with shadow.py (F, R, S), the fixed 0.5 SOL ticket, would-halt logging, the Option A loop with a lock file, the `desk-data` branch and Pages-via-Actions, delivered as a PR that the owner merges from his phone (no direct push to `main`), then runs the day-1 threshold and box-uptime checks.
4. **Days 2-3 scan + verify only; freeze at end of day 3; fills from day 4:** Optional: log your KILL/STOP/PASS prediction with a confidence before day 4. Day 14 (early stop only) and day 28 (the real test) follow the decision table and the Checkpoints.
