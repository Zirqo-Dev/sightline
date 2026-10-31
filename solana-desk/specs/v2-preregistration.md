# Solana Paper Desk v2: pre-registration spec

**Status: DRAFT, pre-registered, not running. v6.5 frozen books unchanged until Nov 11 / Nov 25.** Paper only. Config `v1-v6.5` (books F, R, S) runs untouched to its day-14 (~Nov 11) and day-28 (~Nov 25) judgments. Every number below is fixed. If anything is still open when v2 starts, v2 does not count as pre-registered. **Start** is the first scan after every blocking gap in §2 passes. This file must be committed before anyone views the day-14 data. Public repo, so codenames only and "the owner".

## 1. Why v2
Book state from desk-data `paper.json`, generated 2026-10-31 18:30 UTC (fills since 2026-10-28):

| Book | Closes | Mean net/trade | SD | Winners | `liquidity_pulled` | Equity (of 10 SOL) |
|---|---|---|---|---|---|---|
| F | 31 | -39.6% | 61% | 4 | 15 | 3.82 |
| R | 29 | -26.2% | 82% | 7 | 17 | 6.17 |
| S | 48 | -29.1% | 91% | 9 | 29 | 2.96 |

- **F trails random R.** F met the day-14 early-stop condition on Oct 30. SH-03 is judged on v6.5, not here.
- **SH-01 (safety):** pull exits by pool age at entry were <1h 41/58, 1–6h 0/5, >6h 0/4. In the external backtest, <1h pools were pulled within 30 min 5.8% of the time vs 0.2% for pools >6h. This shows age predicts safety, not profit.
- **SH-05:** the v6.5 pull exit fires when the SOL side drops 40%. Of 6 pull exits with reserve data on Oct 30, only 3 were true drains (96–99%).
- **Outside review (Oct 30):** buying pools under 1h old on a 30-min cron is structurally losing, and faster exits don't fix rugs. About 69% of pump.fun tokens trade for the last time on launch day. BOOST went live 2026-07-21 14:23 UTC, so any backtest must be split there.
- **Data:** the failure rate was 0.739 on Oct 31, and every new entry was voided.

## 2. Prerequisites (blocking unless marked)
| # | Requirement |
|---|---|
| G5 | Data-failure rate < 10% on 7 consecutive days. Rate = failed pool lookups ÷ attempted lookups. |
| G10 | **Universe manifest** (§5). |
| G1 | Reserve snapshot (`sol`, `token`, `k`, `pool`, `t`) for every manifest pool on every scan. |
| G2 | Death log for every pool from first-seen, needed for P. |
| G6 | Per-pool snapshots kept uncapped from first-seen. They replace `reserve_snaps`/`SNAP_CAP = 48` for v2. |
| G8 | Pre-start check: read mint and freeze authority on 20 PumpSwap pools that pass every other S2 rule. If all 20 are revoked, drop the rule and G8 is cleared. Otherwise, log authority per pool and keep the rule. |
| G7 | Create an empty `solana-desk/specs/tests-log.csv`. |
| G4 | X mention series with t0 = tracker first-seen (B, log-only). *Nice-to-have.* |
| G3, G9 | Per-scan holder changes; deployer funding wallet. *Nice-to-have.* |

## 3. Register links
- **Uses as a premise:** SH-01 "Pump.fun pools under 1h old rug before the next scan".
- **Reports alongside v2:** SH-05 "The desk rugs far more often than history predicts". Its true-pull rule is SOL reserve down ≥90%.
- **Cannot test:** SH-02 "Same-scan re-entries after a stop lose more than first entries", because v2 bans re-entry.
- **Not addressed:** SH-03 (v6.5 test), SH-04 (blacklist), WR-01 (Cardano), XB-01 (judged by its own tracker rule).
- **New rows:** SH-06, SH-07 and XB-02. Draft text is in §13.

## 4. Books
**S2: Survivors (judged, SH-06).** S2 enters every manifest pool on the first scan where all of these hold:
- `graduated_pump = true` and pool age ≥ 6h
- SOL reserve ≥ 50
- ≥1 trade and ≥1 sell in `txns.h1`
- top-10 holders < 30% by `top10_ex_pool`, and dev ≤ 10%
- `k` never fell ≥50% between consecutive snapshots since first-seen
- mint and freeze authorities revoked, unless G8 dropped this rule

Each mint is entered at most once.

**P: Death predictor (judged, SH-07, no trades).**
- **Unit:** every manifest PumpSwap pool, scored at its first-seen scan, t0.
- **Features:**
  - log1p of SOL reserve, `k`, age at t0
  - sells and buys h1
  - top-10 %
  - dev %
  - RugCheck `score_normalised`
  - `boost_window`
- **Missing values:** median-imputed from the training half, plus one missing flag per feature.
- **Model:** unregularized logistic regression, no feature selection.
- **Split:** pools ordered by first-seen. Train on the first half and score the second, with a 2h embargo at the boundary.
- **Death:** reserve-implied price ≤ -80% vs t0 at any scan in [t0, t0+2h], or zero swaps over the full 2h.
- **Age-only baseline:** the same model with log1p(age) as its only feature.

**B: Buzz on survivors (log-only, XB-02).** B records spikes on S2-eligible tokens and momentum-matched controls. It is never judged and is never part of the test family.

## 5. Universe manifest and twin
- **Scanner universe:** the existing scan sources and pre-filters with the v6.5 lead limits (`LEADS_PER_RUN = 12`), recorded in the manifest header.
- **Logged on each scan:**
  - `scan_id` and every candidate mint
  - every S2 input as fetched, with its fetch timestamp
  - pass or fail for each rule
  - the twin candidate list and the draws
- **Recomputability:** eligibility must be recomputable from the manifest alone, with no re-fetch or backfill of RugCheck, holders or `txns.h1`.
- **Twin universe:** manifest pools on that scan with `graduated_pump`, age ≥ 6h and ≥1 trade h1, minus S2's picks.
- **Twin draw:** n draws without replacement, at most one entry per mint, using `random.Random(f"{SEED}:{scan_id}")`. The draw is a sample of the candidate mints sorted by address. **SEED = 20261031.** If there are fewer than n candidates, the twin enters all of them and logs the shortfall.

## 6. Shared rules
- Paper only. Fixed notional of 0.5 SOL per trade with no cash limit. Equity is for reporting only.
- No re-entry.
- Dead or unroutable tokens count as -100%.
- **Voids:** a trade is void only if no entry quote was logged. Once it is quoted, it counts. Void rates are reported for S2 vs twin.
- **Shakedown:** the first 7 days after start check the pipeline only. Their returns are not viewed or counted. There is no explore share.
- **Config freeze:** frozen at start. A mid-run bug fix is allowed only if it changes no entry, exit or cost value. Otherwise it starts a new book.
- **Later attempts:** any later book on SH-06 or SH-07 lists the prior attempts, and its result is reported alongside them.

## 7. Fills and costs
- **Prices:** Jupiter quote `outAmount` at 0.5 SOL (`entry_quote_*`, `exit_quote_*`). No cached USD prices.
- **Costs per side:** `v3-exit-aware`:
  - 60 bps base
  - plus 0.25 × verified constant-product impact
  - plus 150 bps on stop exits
  - capped at 600 bps
  - plus 0.00001 SOL network fee
  - plus **0.001 SOL priority fee/tip**
- **Exit failures:** 3 no-route checks in a row, or `pool_gone`, close at 0.
- **Secondary report:** a run at 2× costs. It cannot rescue a fail.

## 8. Exits and labels
**v6.5, for reference (verified in `config.py`/`exits.check`):**
- Liquidity exit when the SOL side drops below 60% of entry.
- -25% stop.
- Sells 1/3 at +40% and 1/3 at +100%.
- 25% trailing stop, active only after +40%.
- 120-min time stop unless up 10%.
- 3 no-route checks → 0.

**v2 (S2 and twin):**
- -35% hard stop, checked at each scan. Gaps past -35% are realized at the quote.
- 24h time stop.
- 3 no-route checks or `pool_gone` → 0.
- Nothing else.

**Labels** are descriptive only and feed no pass or kill rule. They require `same_pool = true`. Precedence is **DEAD > LP_PULL > INSIDER_DUMP > ORGANIC_DECLINE > none**.
- DEAD: `no_route_3x`, `pool_gone`, or no trades for 6h.
- LP_PULL: `exit_k ≤ 0.5 × entry_k`, and both exit reserves are below entry.
- INSIDER_DUMP: `exit_k ≥ 0.8 × entry_k`, and the SOL reserve fell ≥50% between two consecutive snapshots.
- ORGANIC_DECLINE: k kept (≥0.8×), SOL reserve down ≥20%, and no single-gap drop ≥50%.

Severity: WIPEOUT ≤ -90%, HEAVY -90% to -50%, STOP -50% to 0%.

## 9. Judging
**Test family:** F1 = SH-06, which is Holm-corrected across exactly 2 tests at one-sided family α = 0.05:
- (a) S2 mean net % > 0
- (b) S2 mean net % − twin mean net % > 0

P is a separate pre-registered test (F2), judged alone at one-sided α = 0.05. B is in no family.

**Bootstrap:**
- 10,000 resamples, percentile method, seed 1031.
- Clusters = UTC entry day. S2 and twin are resampled together by day.

**S2 pass:**
- Needs 150 post-shakedown closes.
- Both Holm-adjusted one-sided lower bounds must be > 0.
- (a) must stay > 0 after the 2 largest winners are dropped.
- Detectable at 80% power, assuming SD 60%: about +15% absolute and about +21 pts vs twin.

**S2 kill:** kill rules can only stop S2, never pass it.
- At 50 closes: the one-sided 90% upper bound of mean net is < 0, or there are ≥ 5 WIPEOUTs.
- At 100 closes: the point mean (S2 − twin) is < 0.

**S2 cap and floor:**
- Cap is 10 weeks after start. Reaching the cap without 150 closes means **inconclusive**.
- If entries average < 3/day over days 8–21, S2 is abandoned as unworkable.

**Pre-registered outcomes:** if S2 passes (a) but fails (b), and the twin is also profitable, the verdict is **"base edge only"**. That is not a pass for SH-06, and v3 then tests the simpler age/activity rule.

**P pass:**
- Needs ≥ 150 deaths and ≥ 150 survivors in the scored half.
- AUC ≥ 0.70, and AUC ≥ age-only AUC + 0.03.
- The paired bootstrap lower bound of that difference must be > 0.

**P kill:** AUC < 0.62 once the scored half has 75 deaths.

**Tests log:** every test run on v2 data gets a row in `tests-log.csv`, recording date, id, family, metric, result and planned yes/no. Unplanned tests are exploratory and cannot pass anything.

## 10. Abandon v2 if
- S2 is killed or fails, and P is killed or fails.
- G5 data failures exceed 10% on more than 3 days in any week.
- The S2 entry floor is missed.

## 11. Path to live (unchanged)
- Pass with 150+ closes.
- Then 4+ weeks of real-time shadow.
- Then 50 micro-live trades.

The owner approves each step.

## 12. Owner decisions remaining
None. The main S2 tests use a one-sided family-wise significance level of α = 0.05, with a 2-test Holm correction (worst step 2.5% one-sided).

Everything else is fixed above.

## 13. Register text (draft, not created)
- **SH-06 "Survivor pools beat zero and their random twin after costs"**
  - Stage: Hypothesis
  - Statement: S2 entries (PumpSwap pools ≥6h old, ≥50 SOL, active, top-10 <30%) make money after costs and beat a random twin drawn from the same age/activity universe.
  - Test: per v2 spec §9. Validated if both Holm-adjusted one-sided bootstrap lower bounds are > 0 at 150 post-shakedown closes, with mean net > 0 after the top 2 winners are dropped. "Base edge only" if (a) passes but (b) fails with a profitable twin. Invalidated on the kill rules or a fail. Inconclusive at the 10-week cap.
  - Min sample: 150. Judge on: start + 10 weeks.
- **SH-07 "Scanner features predict pool death within 2h"**
  - Stage: Hypothesis
  - Statement: a fixed logistic model on first-seen features ranks PumpSwap pools that die within 2h (≤ -80% or zero swaps) above survivors, beyond pool age alone.
  - Test: per v2 spec §4/§9. Validated if out-of-sample AUC ≥ 0.70 and ≥ age-only + 0.03, with a paired bootstrap lower bound > 0. Invalidated if AUC < 0.62 at 75 deaths, or not validated at ≥150 deaths and ≥150 survivors.
  - Min sample: 150 deaths.
- **XB-02 "Buzz on survivors (log-only)"**
  - Stage: Observation
  - Statement: X spikes on S2-eligible tokens, logged with t0 = tracker first-seen and momentum-matched controls.
  - Test: none. Log-only, never judged in v2. Any future test needs its own pre-registration sized for about 300 spikes.

## 14. Reviewer points not adopted
- **None. All 13 must-fixes and the §12 values are adopted.**
- **One caveat on the power math:** it assumes a per-trade SD of 60%, but v6.5 per-trade SDs are 61–91% (F/R/S above). Those come from a different regime (<1h pools, 2h exits), so the assumption is not changed. If the S2 SD is near 90%, 150 closes detects only about +22% absolute. This is reported at judging and does not change any threshold.

## Changelog
- **Rev 1, Oct 31:** first draft. Cross-checked against the register and repo.
- **Rev 2, Oct 31:** applied review 1. Changes:
  - All values frozen.
  - G10 manifest added.
  - Twin universe and seed defined.
  - Holm family of 2 plus separate P test.
  - Day-clustered bootstrap.
  - 7-day shakedown and 10-week cap.
  - Fixed notional.
  - Exact kill rules.
  - Full P spec with age-only baseline.
  - Label precedence.
  - Void rule.
  - Restart and bug-fix rule.
  - Priority fee and 2× cost run.
  - B made log-only.
  - "Base edge only" outcome.
  - SH-06/SH-07/XB-02 text.
- **Rev 3, Oct 31 2026:** owner fixed alpha = 0.05 one-sided family-wise (Holm, 2 tests). Committed as pre-registration before day-14 data viewed.
