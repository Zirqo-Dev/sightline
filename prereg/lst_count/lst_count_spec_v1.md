# LST dip count, 14-day forward capture spec v1 (drafted Nov 11, 2026 ~2:55 PM EST, for lock by sha)
Owner yes, Nov 11 2:42 PM EST: "Yes, start the 14-day LST dip count now". Count only, no returns, no P&L, no v6.5 reads or writes. $0: public Solana RPC, Jupiter lite quote API, GeckoTerminal free API. No keys, no wallet, no identities. Research only.

## 0. Pre-lock probe facts (Challenger, Nov 11 ~2:50 PM EST, logged so they don't get re-assumed)
- JitoSOL on-chain rate (SPL stake pool Jito4APyf642JPZPx3hGc6WWJ8zPKtRbRs4P815Awbb, total_lamports/pool_token_supply at byte 258) = 1.31094 SOL; last_update_epoch 1083 = current epoch. Jupiter 0.5-unit quotes: sell 1.30964, buy 1.30973 SOL per JitoSOL, i.e. about 9-10 bps under the rate.
- mSOL on-chain msol_price (Marinade state 8szGkuLTAux9XMgZ2vtY39jVSowEcpBfFfD8hXSEqdGC, u64 at byte 512 divided by 2^32) = 1.41738; Jupiter sell 1.41617 (about 8.5 bps under).
- Sanctum extra-api /v1/sol-value/current returned 1.27587 (JitoSOL) and 1.37665 (mSOL), about 2.7-2.9% BELOW on-chain. It is stale or differently defined. It must NOT be the rate source (it would show every LST at a fake 3% premium and hide all dips). Allowed only as a logged cross-check, never in the count.
- So the normal resting gap is about 10 bps. A 30 bps threshold is about 20 bps beyond normal.

## 1. LST set (frozen once, at the first capture after merge; no additions or removals during the 14 days)
Candidates: JitoSOL, mSOL, bSOL, JupSOL, bbSOL, hSOL, vSOL, jSOL, laineSOL, dSOL, plus any other mint whose rate can be read under section 2.
In if ALL hold at freeze, logged with values:
 a) Rate decodable under section 2 (SPL stake pool with pool_mint at byte 162 equal to the LST mint, or Marinade for mSOL). Pool account addresses are listed in a manifest committed with the code and checked against pool_mint at freeze. Mismatch = excluded and logged.
 b) Largest LST/SOL or LST/WSOL pool on GeckoTerminal (networks/solana/tokens/{mint}/pools, page 1) has reserve_in_usd >= $5,000,000.
 c) A Jupiter buy quote for 0.5 SOL succeeds.
Excluded by rule: INF (Sanctum Infinity is a multi-LST pool, not a stake pool), and any LST whose rate needs a paid or keyed API.

## 2. Redemption rate R (SOL per LST), on-chain only
- SPL stake pools: getMultipleAccounts (base64) on the public RPC https://api.mainnet-beta.solana.com. R = total_lamports / pool_token_supply (u64 LE at bytes 258 and 266). Also log last_update_epoch (byte 274) and the withdrawal fee fields as raw.
- mSOL: Marinade state account, msol_price u64 at byte 512 / 2^32. Unit test must pin the offset against a fixture captured at lock.
- Staleness: if an SPL pool's last_update_epoch is less than the current epoch (getEpochInfo, same capture), that LST-hour is MISSING (reason rate_stale). No interpolation.
- Fallback RPC: none. A failed RPC call = MISSING for that hour (no paid RPC, no keyed RPC).

## 3. Quote (the "price")
- Jupiter lite API GET https://lite-api.jup.ag/swap/v1/quote, ExactIn, input SOL (So111...112), output = LST, amount 500000000 (0.5 SOL, the same ticket as the races), slippageBps 50, default routing.
- Buy price P = 0.5 / (outAmount / 10^decimals) SOL per LST. Log outAmount, priceImpactPct, the route labels and the raw response sha.
- Also log a sell quote (0.5 units of the LST to SOL) for display only. The count uses the buy side, because the idea is buying the dip.

## 4. Discount and onset
- D = 1 - P / R, in bps.
- ONSET when D >= 30 bps on a captured hour AND the LST is "armed".
- An LST starts armed. After an onset it disarms, and re-arms only after a captured hour with D <= 15 bps (hysteresis), or 72 h after the onset, whichever comes first. This stops one long dip counting as many onsets.
- If the hour(s) right before an onset are MISSING, the onset still counts but is flagged onset_after_gap (its start time is uncertain).
- Also report (display only) a fee-adjusted D using R x (1 - withdrawal fee). The KILL line uses raw D (lenient, so it won't kill on our own cost assumption).

## 5. Cadence and window
- Hourly at minute 17 UTC (off the :00 congestion), GitHub Actions cron "17 * * * *" plus workflow_dispatch. Each run: getEpochInfo + one getMultipleAccounts + Marinade read + 1 Jupiter buy and 1 sell quote per LST. Up to 2 retries per call within the run, with 20 s and 60 s backoff.
- Window: starts at the first successful capture after merge (T0, logged) and ends at T0 + 336 h. Expected slots = 336 per LST. No extension.
- Output: append-only captures.jsonl and status.json on a data-only branch (lst-count-data). Each write logs its sha and UTC time. GITHUB_TOKEN mapped in the step env, the privacy guard step stays, and the unmapped_github_token static test covers the new workflow.

## 6. Missed captures
- Any LST-hour without both a valid R and a buy quote is MISSING with a reason (no_run, rpc_fail, quote_fail, rate_stale). Never backfilled (quotes can't be fetched historically).
- Coverage = captured LST-hours / (336 x number of LSTs). If coverage < 70%, the count is BLOCKED (not KILL). A fresh 14 days then needs the owner's yes.
- A whole-run miss (cron drop) is logged as no_run from the gap between runs.

## 7. Verdict at T0 + 336 h (one read, no peeking at counts before then except health: runs ok, coverage)
- KILL if total onsets across all LSTs < 4.
- KILL also if onsets >= 4 but they fall on fewer than 3 distinct UTC days. Otherwise one crash day passes the gate while giving the returns test no independent cases.
- PASS (gate only) if onsets >= 4 on >= 3 distinct UTC days and coverage >= 70%. PASS means only "worth pitching a returns book after Nov 25". Evidence stays NONE.
- Report: onsets per LST and per day, max D, how many flagged onset_after_gap, raw vs fee-adjusted counts, coverage per LST, and median resting D.
- Tally: a count gate tests no returns, so it adds nothing to the false-pass tally.

## 8. Hard limits
No P&L, no simulated trades, no returns computed in the 14 days. No v6.5 code, data or schedules touched. No owner identities or wallets. No new spend. Lock this file read-only with sha; any change after lock is an addendum logged before it takes effect.

## 9. Runner choice
GitHub Actions on the public repo: $0 and the same pattern as races and UA-F1, and the new heartbeat (bc-0e1a773b) can watch it. A box runner is also $0 but depends on box uptime, and nothing watches it. Lean: GitHub Actions.
