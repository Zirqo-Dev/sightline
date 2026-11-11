# UA-F1: forward unlock-avoid side test (prereg)
Research only, paper only, not financial advice. Evidence label at lock: WEAK. It's an AVOID signal only ("don't hold or buy this token from T-30 to T+0"), not a trading book. Its own family, m=1, alpha 0.05 one-sided. It isn't pooled with anything. Zero cost: free keyless data only.
Origin: batch-3 #3 (BLOCKED, seen in-sample, closed as a backtest family). This test never re-scores or reuses any batch-3 episode, price or twin.

## §1 Pre-run integrity (new standing rule)
- Before the first fetch of every run (lock run, weekly runs, final run), the job runs `sha256sum -c prereg_ua_f1.sha256` and appends its full output with an ISO timestamp to `run_log.jsonl`.
- If the check fails, the run does nothing else and the test goes to PAUSED until I rule.
- Every deviation is appended to `deviations.md` BEFORE acting. Any claim about order ("before any fetch") must be backed by file mtimes.

## §2 Data path (read-only)
- Schedules: `https://defillama-datasets.llama.fi/emissions/<protocol>` (the public datasets bucket, keyless). The paid `api.llama.fi` emissions endpoints are never used.
- Token prices: the GeckoTerminal public API, daily OHLCV close (USD), on the main pool. Main pool = GeckoTerminal's first SOL/WSOL, USDC or USDT pool for the mint with bars on the registration day, fixed per token at registration and logged.
  - Only if there's no such pool: Binance TOKENUSDT daily close from data.binance.vision, fixed per token at registration.
  - The source is never switched after registration. If the fixed source stops, that episode is unusable.
- SOL: Binance SOLUSDT daily close (data.binance.vision), UTC days.
- The job only reads these sources and writes only to `unlock_forward/` (append-only raw, run_log, episodes, deviations).
- It never reads v6.5, race, desk or S2 files and never writes Notion.
- No signed-in sessions, no X, no paid keys.
- Raw price responses are cached each weekly run, so GeckoTerminal's 180-day wall can't erase them.

## §3 Snapshots and which cliffs count
- S0 = a full snapshot of the bucket for every protocol in the batch-3 SPL-native list, plus any SPL-native protocol in emissionsProtocolsList at lock. It's taken at lock, sha'd and stored.
- Monthly snapshots S1, S2… are taken at 00:00 UTC on the 1st of each month and sha'd before use. Same SPL-native rule: Solana-origin mint confirmed by exchange deposit network or CoinGecko origin platform; no stables, LSTs or wrapped tokens; UXD excluded.
- A cliff counts only if (a) it's in the snapshot taken at or before its T-30, and (b) T-30 is on or after the lock date. So the earliest countable cliff is lock + 30 days. Nothing whose window has started is scored.
- Cliff = a dated cliff event of at least 2% of the schedule's cumulative documented-unlock supply just before it (the batch-3 proxy). TGE days are excluded.
- Same token within 30 days: merge, from T-30 before the first cliff to T+0 of the last. Merge using the schedule as of the registration snapshot.
- Registration: on the first weekly run on or after T-30, the episode is written to `episodes.jsonl` with the snapshot id, cliff dates, amounts, price source, and twin start day (§5). After that it's frozen.
- Later schedule edits (moved, cancelled or added cliffs) don't change a registered episode. They're logged as `schedule_changed` for display. The signal is judged on the schedule as known at T-30.

## §4 Episode score
- Action: hypothetically swap the token to SOL at the T-30 daily close and back at the T+0 daily close.
- Gross = SOL return minus token return, each = close(T+0)/close(T-30) - 1.
- Net at 1% = gross - 1.0%. Net at 2% = gross - 2.0%.

## §5 Random twin
- Same token, a 30-day window [s, s+30], scored exactly like §4.
- s is drawn at registration with numpy.random.default_rng(20261125 + episode_index). episode_index = registration order, with ties broken by symbol.
- s is drawn uniformly from the token's clean start days in [max(lock date, T-90), T+30]. Clean = no cliff of at least 2% (registration snapshot) and no TGE in [s-30, s+60].
- So the twin is also forward and resolves by about T+60.
- If no clean day exists, or the twin lacks closes, the episode is unusable (logged).

## §6 Data BLOCK rules (per episode, daily closes, episode and twin windows)
- Any day-to-day close move over 50% on a non-meme (meme = CoinGecko meme category or the Jupiter `meme` tag at registration) means the episode is BLOCKED (listed).
- Where Binance lists the token: a gap over 20% between the on-chain close and the Binance close on any day means the episode is BLOCKED.
- Usable = registered, not BLOCKED, with both episode and twin closes.

## §7 Judgment: once only
- Judgment happens when the 30th usable episode has both its episode and twin resolved. The first 30 usable episodes by T+0 order are judged; any later ones are display only.
- PASS needs ALL of:
  1. mean episode net at 2% > 0 (the episode leg alone);
  2. mean (episode net - twin net) > 0;
  3. cluster-robust t (clusters = token, CR0) ≥ 2.0 on (episode - twin), with a one-sided token bootstrap p ≤ 0.05 (10,000 resamples, seed 20261126);
  4. n ≥ 30 usable;
  5. still > 0 after dropping the 2 largest (episode - twin);
  6. if any token is more than 25% of the 30, the mean without it stays > 0.
- Display only: halves (first 15 vs last 15), net at 1%, T-14 to T+14, and `schedule_changed` episodes split out.
- Verdicts:
  - PASS.
  - KILL (any item fails).
  - BLOCKED (usable < 70% of registered episodes whose windows have closed).
  - INCONCLUSIVE (see §8).
- A PASS upgrades the label to MODERATE at most. Adopting it anywhere is a new prereg plus the owner's yes.

## §8 Stop and close conditions
- No interim looks at outcomes. Until judgment, the status page shows only counts: registered, resolved, usable, BLOCKED and unusable, with reasons. No running means, t or win rates anywhere: page, chat, Notion, or logs a bot reads.
- Hard stop: 14 months after lock. If fewer than 30 usable episodes are resolved by then, the verdict is INCONCLUSIVE, and the test closes with no pass.
- Close as BLOCKED early if:
  - the datasets bucket is removed, paywalled or stale for over 60 days;
  - GeckoTerminal's free daily OHLCV ends for over 30 days with no fixed fallback;
  - usable falls below 70% of resolved episodes after at least 20 have resolved.
- Pause (no scoring) if a §1 check fails or the free-call budget would require spending money. Zero spend is a hard rule.
- The owner may stop it at any time. Stopping is final and never re-run on the same episodes.

## §9 Calls and cadence
- Weekly run, Mondays 01:00 UTC (off the race's :12/:42 minutes), on the box or in an existing workflow only. No new paid runner.
- About 20-40 GeckoTerminal calls a week, plus data.binance.vision zips and 1 bucket list a month.
- On a 429, back off and retry next week. A missed week is filled from cache or from GeckoTerminal's 180-day window. Gaps are logged, never interpolated.

## §10 Firewall
- No Nov 25 v6.5 decision, race decision, S2 v3 design or desk rule may cite UA-F1 before its judgment.
- It doesn't touch the locked race v2 (sha 5f52f688…). A possible "unlock avoid" variant of R3/R5 is a separate prereg after judgment.
- The false-pass tally stays at 7 tests until UA-F1 is judged, then it becomes 8.

## §11 Expected pace (stated now)
About 3 qualifying episodes a month at batch-3 rates, starting about lock + 30 days, so about 11 months to reach 30. It's a slow book. Batch 3 had thin twin capacity for debridge and solayer, so expect some twins to repeat or be unusable.

## §12 Known weaknesses (written in)
- The schedules are DefiLlama models, not on-chain transfers. An on-chain-confirmed subset is display only, and only if a free keyless source exists. No extra data hunt.
- The signal assumes a hypothetical holder. The owner holds none of these tokens, so the practical use is as a buy-avoid filter.
- No single test can rule out a market-wide alt-vs-SOL drift beyond what the twin absorbs.
