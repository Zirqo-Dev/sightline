# Sightline wallet race — pre-registration (LOCKED)

**Status: LOCKED.** This file is the race pre-registration for wallets R1–R4 plus the SOL hold. It was locked after the owner's 12:14 PM America/Toronto (EST), 11 Nov 2026 approval. The SHA-256 of this exact file is recorded in `prereg_races.lock`, which is not part of the hashed bytes.

Codenames only. "The owner" means the project owner. No names, handles, emails, wallets, keys or tokens appear here.

Paper only. Display only. Zero spend. Public repo.

---

## 0. Sources and owner decisions

- **Rules of record for the race:** Sightline Challenger's wallet-race specification, 12:12 PM America/Toronto (EST), 11 Nov 2026, used verbatim for the race section, wallets R1–R4, fills, isolation, and the contamination rule. Items the specification left open are filled only in §15 (choices made at lock) with conservative fixed values.
- **Owner decisions, 12:14 PM America/Toronto (EST), 11 Nov 2026:**
  - Race all 4 wallets R1–R4 plus the SOL hold, display-only.
  - Show R1 and R2 results now (do not hide them until 26 Nov).
  - Start the build. Merge only after Challenger's gate and the owner's yes.
- **Scope of this lock:** R1, R2, R3, R4, and the SOL hold only. A later fifth display wallet would require its own written rules, a new owner yes, and a new lock or a separately labelled book. It is not part of this pre-registration and does not reserve Jupiter calls.
- **v6.5 F/R/S comparison sources** (spec/prereg documents only, via cursor-github, default branch `main` of Zirqo-Dev/sightline; no desk-data and no book results were read):
  - `solana-desk/docs/solana-paper-desk-plan-v6.5.md` (config v1-v6.5, frozen 26 Oct 2026), blob SHA `72e4369adcc2ca523d18c7977ffefb7f75821b43`.
  - `solana-desk/specs/v2-preregistration.md` (v6.5 exit reference in §8, and the statement that F/R/S stay on frozen v6.5 rules through the day-14 / day-28 judgments), blob SHA `68fea24a441b5829f089c91b503d45c6937282e9`. No book-result tables in that file were used for this race or this comparison.

---

## 1. Ground rule (on every surface)

> Display only, untested. A lead in the race is luck until tested.

No "winner" is ever called evidence. The board says **"leading so far"**. Nothing is promoted, killed or retuned because of its rank.

**Ranking (official):** net % per 30 days, with n trades and a 95% interval. The wallet with the highest official net % is labelled "leading so far", never "winner" or "proven".

**Official unit:** net percentage of each wallet's own paper equity, measured in SOL. The SOL hold is then exactly 0.00% and is the bar to beat in SOL terms. A companion USDC column is shown so a SOL rally is visible; it never changes official rank.

**30-day window:**

- After a wallet has at least 30 calendar days of equity history, official net % = `equity_now / equity_30d_ago - 1`, including open positions at the latest Jupiter mark (or 0 if marked dead).
- Before that, show net % since start and the day count, labelled "so far, N days". Do not annualize or scale up.
- Until every racing wallet (R1–R4 and the SOL hold) has 30 days, official rank uses the since-start net % over the same elapsed window, with the day count shown.

**95% interval:** a day-clustered bootstrap of mean per-trade net % in SOL, 10,000 resamples, percentile method, seed `20261111`. Clusters are UTC entry date. Open marks are not trades. If a wallet has fewer than 5 closed trades, the interval is shown as `n/a`. The SOL hold has no per-trade interval; show `n/a` and the equity path only.

**Independence from results:** a lead, a lag, a wide interval, or a dead-token streak does not change a rule, a cap, a cost, an exit, or a schedule.

---

## 2. Isolation from v6.5 (contamination rule, verbatim)

The race does **not** read desk-data at all, not even the v2 manifest. It finds pools on its own through free keyless sources (GeckoTerminal new pools or DexScreener, public RPC for holder and mint checks).

- Keyless lite-api only. Its own workflow `races.yml`, concurrency group `sightline-races`, branch `races-data`. Runs twice an hour at `:12` and `:42`, with a 5-minute timeout.
- Cap of 40 Jupiter calls per run. Per-wallet sub-caps, no borrowing, skip and log on a cap or 429.
- Stdlib only, its own fill module, the privacy guard unedited, the repo public, zero spend.

R1 and R2 trade the same kind of pool as the frozen v6.5 meme books. Close matches are labelled on the board as `close variant of v6.5 [book]` (see §14). Running them does not touch v6.5 data: separate workflow, key and branch, and no desk-data reads. The only risk is unblinding. Their results give a rough proxy for how v6.5-style trades are doing before 25 Nov.

**Rule, fixed now:** no 25 Nov decision, early stop or rule change may cite race results, and race results never appear in v6.5 reports.

**Owner's choice, fixed now:** R1 and R2 results are shown now. They are not hidden until 26 Nov.

---

## 3. Infrastructure and cadence

| Item | Fixed value |
|---|---|
| Workflow | `.github/workflows/races.yml` |
| Cron (UTC) | `12,42 * * * *` (minute 12 and minute 42 of every hour) |
| Timeout | 5 minutes |
| Concurrency group | `sightline-races`, `cancel-in-progress: false` |
| Data branch | `races-data` (code stays on `main` via PRs the owner merges) |
| Jupiter | Keyless `https://lite-api.jup.ag/swap/v1/quote` only. Never a keyed `api.jup.ag` call, never mid, never a cached USD price as a fill. Request parameter `slippageBps=50` is only used to obtain a route; the paper fill is `outAmount`, and cost is rule (d), not this parameter. |
| Languages / deps | Python stdlib only (no third-party packages) |
| Fills | Own fill module, not imported from `solana-desk/desk/` |
| Privacy | Existing privacy guard, unedited, on every push |
| Spend | Zero. No paid API, no key, no card |
| Paper | Always. No wallets, keys, signing or swaps |

**Run slot.** Each run has a scheduled slot timestamp `RUN_SLOT`, the UTC time with the scheduled minute (12 or 42) and seconds and microseconds set to 0, even if the job starts late. Seeds, age windows, the +15-minute R4 gate, the 4h and 24h time stops, the monthly R3 rebalance, and the "at most 10 a day" UTC day all use `RUN_SLOT`, not the wall-clock start.

**Missed or late runs.** A missed slot is not filled later. Open positions stay at their last mark until the next successful run. A late run still belongs to its scheduled slot and must finish or be killed at the 5-minute timeout; partial work is committed only if no entry, exit or cost value is invented, otherwise the run is logged as `timeout` and any in-flight quote without a logged fill is dropped (no entry).

**Sources the race may call (keyless only):**

| Use | Endpoint |
|---|---|
| New-pool discovery (supplement only) | `GET https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page={p}&include=base_token,quote_token,dex` with header `Accept: application/json;version=20230203` |
| Post-graduation pool lists (primary discovery) | `GET https://api.geckoterminal.com/api/v2/networks/solana/dexes/pumpswap/pools?page={p}&include=base_token,quote_token` and `GET https://api.geckoterminal.com/api/v2/networks/solana/dexes/letsbonk-fun/pools?page={p}&include=base_token,quote_token` (same Accept header) |
| Pair confirm, SOL reserve, `txns.h1` | `GET https://api.dexscreener.com/latest/dex/pairs/solana/{pool}` and, if needed, `GET https://api.dexscreener.com/latest/dex/tokens/{mint}` |
| Holders, mint / freeze authority, pool SOL vault | Solana public RPC `https://api.mainnet-beta.solana.com` (`getTokenLargestAccounts`, `getMultipleAccounts` / `getAccountInfo` jsonParsed, `getTokenSupply`, and the pool's WSOL vault balance when needed) |
| Paper fills | `GET https://lite-api.jup.ag/swap/v1/quote` |
| R3 meme category | CoinGecko keyless `GET https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category=meme-token&per_page=250&page={p}` and coin detail `GET https://api.coingecko.com/api/v3/coins/{id}` for the Solana mint (`platforms.solana`) |
| R3 Binance listing date | (i) `GET https://api.binance.com/api/v3/klines?symbol={SYM}&interval=1d&limit=1&startTime=0`; (ii) on HTTP 451 or block, first-party public files under `https://data.binance.vision/data/spot/monthly/klines/{SYM}/1d/` (earliest month, first daily row). A 451 is not itself a pass. |
| R4 announcements | `GET https://api-manager.upbit.com/api/v1/announcements?os=web&page={p}&per_page=20&category=trade` and detail `GET https://api-manager.upbit.com/api/v1/announcements/{id}?os=web` |
| R4 first-listing and network cross-check | `GET https://api.upbit.com/v1/market/all?isDetails=true` and `GET https://api.upbit.com/v1/status/wallet` |

The race must not read `desk-data`, the v2 manifest, v6.5 `trades.csv` / `paper.json` / `log.json`, or any v6.5 result file. It must not call keyed Jupiter, paid GeckoTerminal/CoinGecko, or any desk-data proxy.

---

## 4. Fills and honesty rules (every wallet)

Fills for every wallet, including the SOL hold's marks where they apply, follow honesty rules (a) to (e).

**(a)** Logged Jupiter quote at 0.5 SOL, never mid. Entry and exit prices are the `outAmount` of a fresh lite-api quote. Ticket size is 0.5 SOL per entry (R3: 0.5 SOL per name). No mid-price, no GeckoTerminal or DexScreener price, and no cached USD figure may be used as a fill. Amount is `500000000` lamports of wrapped SOL `So11111111111111111111111111111111111111112`.

**(b)** No route, or a failed sell simulation, on 3 runs in a row, or pool gone, means the position is marked at 0. A sell simulation is a lite-api exact-in quote of the full remaining token amount back to SOL. `pool_gone` is a confirmed absence of the pool (DexScreener/GeckoTerminal 404 or empty pair, or RPC account gone). Three consecutive *failed* runs count; see (e) for temporary errors.

**(c)** Mint and freeze authority revoked, plus a sell-quote check at entry, or skip. Both authorities must be null/revoked at the entry run, read from public RPC. The sell-quote check is a lite-api quote that would sell the tokens just bought (or, if the buy quote has not yet been sized, a 0.5 SOL-equivalent sell) and must return a route. Failure is a skip, not a voided fill. A skip is final for that run; the mint may be drawn again only if it is still eligible later and the wallet has never entered it (no re-entry, §15).

**(d)** Cost per side: 60 bps + 0.25 × impact + 150 bps on stop exits, capped at 600 bps. Impact is the verified constant-product impact in bps, not Jupiter `priceImpactPct`. For a quote of Δx SOL into a pool with SOL reserve x, `impact_bps = 10000 * Δx / (x + Δx)`. If the SOL reserve is missing, that side's cost is the 600 bps cap and the fallback is logged. "Stop exits" means R2's −20% hard stop only. Time stops, take-profits, monthly rebalances, R4's 24h exit, and marks at 0 are not stop exits and do not take the 150 bps add-on. The cap is 600 bps per side. No extra network or priority fee is added; Challenger's race cost line is used as written.

**(e)** A cap hit, HTTP 429, timeout, HTTP 5xx, or other temporary quote failure: no new entry that run (skip and log). Open positions keep the last good mark, flagged `stale`, and are retried next run. Stale or temporary-error runs do not count toward the 3-in-a-row no-route streak in (b). They are not marked at 0 unless rule (b) is met. Challenger named rules (a)–(e) and listed four fill bullets; (e) is this temporary-error / cap clause (see §15).

**Voids.** A trade is void only if no entry quote was logged. Once quoted, it counts, including if the later exit is 0.

**Jupiter caps, per run (sum exactly 40; no reserved fifth-wallet slice):**

| Wallet | Sub-cap |
|---|---|
| R1 | 14 |
| R2 | 14 |
| R3 | 7 |
| R4 | 3 |
| SOL hold | 2 |

No borrowing across wallets. If a wallet hits its sub-cap, or if the run hits the 40 cap, or if a quote returns HTTP 429, that wallet skips further quotes, logs the reason, and retries next run under (e) for open marks. It does not enter a new name without a logged entry quote.

**Quote priority inside a wallet's sub-cap:** (1) exit and mark quotes for open positions, oldest first; (2) the entry sell-quote check and entry quote if a draw or signal occurred; (3) unused cap is left unused (never lent).

---

## 5. Launchpads and graduation detection (R1 and R2)

**Exact launchpads (closed set):**

| Launchpad | Eligible graduated dex | GeckoTerminal dex id | Not eligible |
|---|---|---|---|
| pump.fun / PumpSwap | PumpSwap AMM | `pumpswap` | Bonding-curve dex `pump-fun` |
| letsbonk | letsbonk.fun AMM pools | `letsbonk-fun` | Any other dex, including `raydium-launchlab`, which is a different launchpad and is not treated as letsbonk's curve |

No other launchpad (including Jupiter Studio, Believe, Moonshot, or Meteora DBC) is in the race universe. The per-dex GeckoTerminal path `.../dexes/{dex}/new_pools` returned HTTP 404 on 11 Nov 2026 and is not used.

**Graduation event.** A token has graduated when a SOL-quoted pool exists on an eligible graduated dex in the table above. Bonding-curve dexes are never candidates. Quote mint must be native/wrapped SOL `So11111111111111111111111111111111111111112`. USD1-quoted, Bonk-quoted, or other-quote pools are out (they cannot satisfy "SOL reserve of at least 50").

**T_grad.** `pool_created_at` on GeckoTerminal for the first such graduated SOL-quoted pool, in UTC. If DexScreener `pairCreatedAt` (unix ms) is present and earlier, still use GeckoTerminal `pool_created_at` as the official clock (one clock, no mixing). If GeckoTerminal is missing and only DexScreener is present, use `pairCreatedAt` and log the fallback.

**Age window at `RUN_SLOT`:** `3600 <= (RUN_SLOT - T_grad) <= 21600` seconds (1h inclusive through 6h inclusive).

**SOL reserve floor:** at least 50 SOL on that graduated pool's SOL side, read at the same run. Source order: (1) DexScreener `liquidity.quote` when `quoteToken.address` is WSOL, or `liquidity.base` when `baseToken.address` is WSOL; (2) the pool's WSOL vault balance on public RPC; (3) if neither can be read, skip and log — do not convert `reserve_in_usd` into SOL.

**Discovery procedure, each run (keyless, coverage logged):**

1. Paginate GeckoTerminal `.../dexes/pumpswap/pools` and `.../dexes/letsbonk-fun/pools`, pages 1 to at most 6 per dex (12 calls), spaced to respect the public ~10 calls/min limit. Keep SOL-quoted pools whose `pool_created_at` is inside the age window and whose SOL reserve (once confirmed) is ≥ 50.
2. Also paginate GeckoTerminal `.../networks/solana/new_pools`, pages 1 to at most 2, and keep rows whose dex id is `pumpswap` or `letsbonk-fun` and that pass the same quote, age and reserve rules. This feed is only seconds deep per page and is dominated by `pump-fun` bonding-curve rows; it is a supplement, not the clock. (The per-dex `new_pools` URL is 404 and is not called.)
3. Confirm each surviving pool on DexScreener when the pair endpoint resolves. If DexScreener disagrees on quote mint (not WSOL), drop the candidate and log it.
4. Log coverage every run: pages fetched, HTTP failures, oldest `pool_created_at` seen on each dex list, number of window-eligible pools, number dropped for missing reserve, and any 429s.

These GeckoTerminal dex pool lists are volume-ranked, so quiet graduates in the 1–6h window may be missing. That shortfall is accepted, logged, and is not filled from desk-data or a paid API.

If a run cannot fetch at least one page from both dex list endpoints, it still uses whatever pages arrived; a zero-candidate run is allowed and is logged. There is no backfill from desk-data.

---

## 6. Seeding and the R1/R2 draw

Shared candidate universe at slot t, **before R2's extra filters**:

`U_t` = unique mints that pass §5 at `RUN_SLOT` (graduated from the closed launchpad set, SOL-quoted, age 1–6h, SOL reserve ≥ 50), sorted lexicographically by mint address (UTF-8 / base58 string).

**Seed (exact):**

```
RUN_SLOT_ISO = RUN_SLOT formatted as YYYY-MM-DDTHH:MM:00Z
SEED_MATERIAL = f"{RUN_SLOT_ISO}|R1R2"
SEED = int.from_bytes(hashlib.sha256(SEED_MATERIAL.encode("utf-8")).digest()[:8], "big")
rng = random.Random(SEED)
```

`hashlib` and `random` are the Python 3 stdlib modules. This is the only seed used for R1 and R2.

**Draw (exact):**

```
if U_t is empty:
    no draw, no entry, log empty_universe
else if rng.randrange(6) != 0:
    no entry this slot (the 1/6 gate failed), log no_draw
else:
    chosen = U_t[rng.randrange(len(U_t))]
```

Probability of a draw, given a non-empty `U_t`, is exactly 1/6. Expected pace at 48 slots/day with a non-empty universe is 8 draws a day, matching "about 8 a day", then capped.

**Paired application (same draw, so R2 vs R1 is a fair look at filters and exits):**

- The draw is computed once from `U_t` and `SEED` as above.
- **R1** enters `chosen` if honesty rule (c) passes, the wallet has never entered this mint (no re-entry), and R1's UTC-day fill count is < 10.
- **R2** enters the **same** `chosen` if, in addition, every R2 filter in §8 passes, (c) passes, no re-entry, and R2's UTC-day fill count is < 10.
- If R2's filters fail, R2 skips and logs the failed filter. R1 may still enter. That is the comparison: same token, same slot, filters-and-exits on or off.
- There is no redraw when a skip occurs. A skipped draw does not count toward either wallet's daily cap.

**Daily cap:** at most 10 filled entries per wallet per UTC day. The cap counts fills, not skipped draws. At most one new entry per wallet per slot.

---

## 7. R1 — 'Graduate sampler' (busy random baseline, about 8 a day)

Yardstick: "what does a random meme do".

- Candidates: `U_t` in §5–§6.
- Entry: the paired draw in §6, honesty (a)–(e). At most 10 fills a day.
- Exit **only** at the fixed 4h time stop, or at 0 if dead under rule (b). No take-profit, no stop, no liquidity exit, no trailing stop.
- The 4h time stop closes at the first run with `RUN_SLOT >= entry_slot + 4 hours`. `entry_slot` is the `RUN_SLOT` of the fill.
- Board label: `close variant of v6.5 R` (see §14).

---

## 8. R2 — 'Filtered fast-exit meme' (busy, about 5–10 a day)

Same candidate pool as R1 (`U_t`), plus filters, plus a different exit. Same random draw as R1 (§6). "This is the S2 exit variant without S2" in Challenger's wording; the locked exits below are the ones in that same message (+25% take-profit, −20% stop, 4h time stop, or 0 if dead) and are not v2 S2's −35% / 24h exits.

**Filters, all required at the entry run (in addition to §5 and honesty (c)):**

| Filter | Rule | Source |
|---|---|---|
| Top-10 holders | < 30% after excluding the pool, bonding-curve, and locker accounts (same exclusion idea as v6.5; the 30% threshold is R2's, not v6.5's 35%) | Public RPC. If the holder read fails (including HTTP 429), skip and log. Do not fall back to RugCheck. |
| Dev | ≤ 10% | Public RPC plus launchpad/DexScreener creator if a single creator address is present; otherwise skip and log |
| Sells | at least 1 sell in the last hour | DexScreener `txns.h1.sells >= 1`, else GeckoTerminal `transactions.h1.sells >= 1`. If both fields are missing, skip and log |
| Authorities | mint and freeze revoked | already required by honesty (c), public RPC |

**Top-10 and dev, mechanical:**

- Total supply from `getTokenSupply`.
- Top accounts from `getTokenLargestAccounts` (read at least 20, then exclude, then take 10). For each account, read jsonParsed token-account data.
- Exclude an account if its address or its token-account owner is the pool address, or if its token-account owner is one of: Pump.fun bonding-curve program `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`, PumpSwap AMM program `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`, the owner program of the candidate pool account itself (so letsbonk vaults are excluded without inventing a program id), or the mint address. Locker programs that cannot be identified mechanically are not excluded (this can only raise top-10 % and make the filter tighter).
- Top-10 % = sum of the remaining largest 10 balances / total supply.
- Dev wallet, first match only: (1) a single `creator` / `deployer` / `dev` address on the DexScreener or GeckoTerminal payload for this pool; (2) else the Metaplex metadata update authority if present and not a known program; (3) else skip the mint for R2 (fail closed). Dev % is that wallet's share of total supply, summing its token accounts, except that a pool vault so identified counts as 0 toward dev.

**Exits (first hit wins, checked at each run):**

1. Take-profit at +25% net of the entry-side cost already paid, marked at the current sell quote after the exit-side cost. Close the full remaining position (one take, not a ladder).
2. Hard stop at −20%, same marking. This is a stop exit for cost rule (d) and takes the +150 bps.
3. 4h time stop, first run with `RUN_SLOT >= entry_slot + 4 hours`. Not a stop exit for the 150 bps add-on.
4. Marked at 0 if dead under rule (b).

Board label: `close variant of v6.5 F` (see §14).

---

## 9. R3 — 'Established meme basket' (ecosystem-rally beta)

**Eligibility rule, fixed now. The actual five-name list is resolved at the first successful run and written to `races-data`; it is not chosen in this lock.**

A token is eligible if and only if all of the following hold:

1. **SPL-native:** a Solana mint (not a wrapped asset whose only official issuance is on another chain). The mint is the Solana address in CoinGecko `platforms.solana` and the address used on Jupiter.
2. **Meme:** listed in CoinGecko's `meme-token` category at the list-build run (keyless CoinGecko, §3). If CoinGecko is unreachable that run, retry on later runs and keep R3 idle until the list is built. CoinGecko `atl_date` is not used.
3. **Binance spot listed for at least 12 months as of 11 Nov 2026:** first Binance *spot* listing date `<= 2025-11-11 23:59:59 UTC`. Futures-only, pre-market, or date-unproven listings do not count. Source order: (i) Binance `klines` first daily open time, if reachable; (ii) first-party `data.binance.vision` monthly 1d klines, earliest month, first row; (iii) otherwise the token is **excluded**. A Binance HTTP 451 is not a pass; it only triggers the vision fallback. Listing-date eligibility is frozen as of 11 Nov 2026: a token that later reaches 12 months of Binance spot history is not added.
4. **Jupiter route at the list-build run:** lite-api returns a route both ways at 0.5 SOL (buy from SOL and sell back to SOL).

**First-run resolution:**

- Compile the eligible roster with the rule above. Write the roster (mint, symbol, listing-date source, listing date) to `races-data`.
- Rank eligible names that still have a two-way Jupiter route by **Jupiter liquidity** at that run. Liquidity rank key, in order: (1) ascending verified constant-product impact of a 0.5 SOL buy into the primary SOL pair (lower impact = deeper); (2) if CP impact cannot be computed, ascending Jupiter `priceImpactPct` on that same 0.5 SOL quote, logged as `rank_impact_source=jup_pct`; (3) ties broken by mint address ascending. `priceImpactPct` is used only for this rank fallback, never as a fill or as cost.
- Take the top 5. If fewer than 5 qualify, take all that do and log a shortfall. If none qualify, R3 is off for the race and logged.
- Enter each selected name at 0.5 SOL, equal weight. First-run notional is 0.5 SOL × n_names.

**Rebalance:** on the 1st of each month, at the first run with `RUN_SLOT >= 00:12:00Z` on that date. Re-apply eligibility (still "listed ≥ 12 months as of 11 Nov 2026", not a rolling 12 months), require a current two-way route, re-rank by then-current liquidity, take the top 5 (or all if fewer). Sell names that leave the basket, buy names that enter, and restore equal weights of then-current R3 equity (in SOL), in 0.5 SOL tickets (a remainder under 0.5 SOL stays in SOL until it can be applied). Honesty (a)–(e) apply to rebalance fills. Rebalance exits are not stop exits. If that run is missed, or the sub-cap is exhausted mid-rebalance, the next successful run finishes the rebalance and logs `late_rebalance`. Expected pace: about 5 trades a month.

R3 does not use the R1/R2 draw or launchpad window.

---

## 10. R4 — 'Listing bounce, live' (rare, about 1 a month)

Upbit only, because Binance's API returns HTTP 451 to US GitHub runners.

**Reachability gate.** At the first 2 runs, `GET https://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=20&category=trade` must return HTTP 200, `success: true`, and a parseable `data.notices` array. If either of those two runs fails that check (including a block, timeout, or empty/unparseable body), R4 is off for the race and the failure is logged. It is not silently retried later.

**Endpoints:**

- List: `GET https://api-manager.upbit.com/api/v1/announcements?os=web&page={p}&per_page=20&category=trade`
- Detail: `GET https://api-manager.upbit.com/api/v1/announcements/{id}?os=web`
- Market cross-check: `GET https://api.upbit.com/v1/market/all?isDetails=true`
- Wallet / network: `GET https://api.upbit.com/v1/status/wallet`

**Event.** An Upbit announcement of a **first** listing of an **SPL-native** token.

- Title (Korean or English) must match at least one first-listing pattern and none of the exclusion patterns.
  - First-listing patterns (closed set): `디지털 자산 추가`, `신규 거래지원`, `Market Support for`, `Trading Support for`.
  - Exclusion patterns (closed set): `유의`, `종료`, `중단`, `Investment Warning`, `Delist`, `Trading Support Termination`, `Suspension`.
- Announcement time `T_ann` is `first_listed_at` (not `listed_at`), converted from its stated offset (typically `+09:00`) to UTC. If a timestamp has no offset, treat it as Asia/Seoul and log that fallback.
- SPL-native proof, all required: (1) `status/wallet` `net_type` for the ticker includes `SOL` or `Solana`, or the detail body's network cell names Solana (`Solana`, `SOL`, or `솔라나`); (2) a unique Solana mint resolved from the announcement (a single mint in the official body, else a unique CoinGecko `platforms.solana` mint whose symbol matches the ticker exactly). If the mint is missing or ambiguous, skip and log; do not guess.
- **First listing only.** Adding a new quote market for an asset already listed on Upbit is not an event. Proof of "already listed": an earlier trade-category announcement for the same symbol with an earlier `first_listed_at` (pages 1–10 of the list endpoint, fetched at detection), or a market (`KRW-`, `BTC-`, or `USDT-` + symbol) that existed before `T_ann`. When that history cannot be checked, skip and log (do not enter).

**Entry:** at the first run with `RUN_SLOT >= T_ann + 15 minutes`, honesty (a)–(e). At most one open R4 position at a time; further qualifying events wait in announcement order (`T_ann`, then announcement id) and are not skipped silently.

**Exit:** at the first run with `RUN_SLOT >= entry_slot + 24 hours`, or at 0 if dead under (b). No take-profit, no stop. The 24h clock starts at entry, not at `T_ann`.

**Batch-3 wall.** R4's display period never counts toward batch-3 #1 (listing drift). If batch-3 #1 passes, a tested wallet starts fresh with its twin; this display book is not that wallet.

R4 will mostly sit idle. That is expected.

---

## 11. Benchmark — SOL hold

- From the first successful run, the wallet holds 0.5 SOL and never trades.
- Official net % in SOL is 0.00% at every mark (the numeraire). No entry or exit cost in SOL terms.
- Companion USDC marks: a lite-api quote of 0.5 SOL → USDC (`EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`) each run, under the SOL hold's sub-cap of 2. These marks are display-only.
- No stops, no time exit, no death rule. Three failed SOL/USDC quotes do not mark the hold at 0; the last good USDC mark is kept and flagged stale.

---

## 12. What this race is not

- Not a test. Not evidence. Not a v6.5 book, twin, or early look that may be cited on 25 Nov.
- Not a copy-trade of any third-party wallet.
- Not a promotion path: a tested book that later passes a locked backtest joins the board as a new wallet, with its forward n starting at zero. Backtests never count as forward results. Race rank never promotes or kills a book.
- Bug fixes after start may not change an entry, exit, cost, cap, seed or universe rule; otherwise the changed wallet starts a new, separately labelled book.

---

## 13. Line-by-line comparison of R1 and R2 with frozen v6.5 F/R/S

Spec text used: `solana-desk/docs/solana-paper-desk-plan-v6.5.md` (frozen 26 Oct 2026, blob `72e4369adcc2ca523d18c7977ffefb7f75821b43`) and the v6.5 exit restatement in `solana-desk/specs/v2-preregistration.md` §8 (blob `68fea24a441b5829f089c91b503d45c6937282e9`). No desk-data results were read. No `config.py` was read; the plan's own "config.py starting values (v6.5)" table is the frozen text used here.

v6.5 books, from the plan's "Shadow books" and config table:

- **F (filtered):** leads that pass every safety filter and the execution checks. The desk.
- **R (random):** random sample of leads from the same runs where F fills, about F's daily count, capped at 2× F; must pass the execution checks; safety filters ignored.
- **S (rule-skipped):** tokens failing a safety filter but passing the execution checks, same cap as R.

Shared v6.5 execution checks (all books): Jupiter route exists; sell-side impact at size at most 3%; ticket at most 5% of the pool's SOL side; at least 1 sell in the last hour.

Shared v6.5 safety filters (F must pass; S fails these; R ignores them): not graduated (bonding-curve tokens, `pumpfun` / `meteoradbc`, skip `not_graduated`); 10 SOL SOL-side floor; LP lock by pool type (90% on DAMM/Orca/Raydium; DLMM not applicable; PumpSwap no signal); mint/freeze authority open = skip; max dev 10%; max top-10 35% after excluding pool, curve and locker accounts; RugCheck `score_normalised` ≤ 40; age 15 min to 48h; rugged / `danger:*` flags.

Shared v6.5 fills: paper, fixed 0.5 SOL ticket, Jupiter `outAmount`, costs `v3-exit-aware` (60 bps + 0.25 × verified CP impact + 150 bps on hard_stop / trailing_stop / liquidity_pulled, cap 600 bps). Confirmed gone or no route 3 checks = −100%.

Shared v6.5 exits (all of F, R and S): hard stop −25%; ladder sell 1/3 at +40% and 1/3 at +100%; 25% trailing stop after the first take (after +40%); liquidity exit when the SOL side drops below 60% of entry (liq_drop 40); 120-min time stop unless up 10%; 3 no-route or pool gone = 0 / −100%.

Universe: DexScreener paid feed (token profiles and boosts), Solana, `LEADS_PER_RUN = 12`. Cadence 10–30 min (Actions fallback every 30 min). F at most 4 open and no more than 8 new F entries/day.

### 13.1 Dimension table

| Dimension | v6.5 F | v6.5 R | v6.5 S | R1 | R2 |
|---|---|---|---|---|---|
| Lead source | DexScreener paid profiles/boosts | Same runs as F | Same runs as F | Own keyless GeckoTerminal / DexScreener scan of pump.fun/PumpSwap and letsbonk graduates; no desk-data, no paid-feed requirement | Same as R1 |
| Launchpads | Graduated required for F (safety); bonding-curve out for F only. R may enter ungraduated tokens because it ignores safety filters | Same universe, safety ignored | Fails a safety filter | Closed set: PumpSwap or letsbonk-fun graduates only | Same as R1 |
| Age | 15 min–48h (pair age on the feed) | Same | Same | 1–6h since graduation (`T_grad`) | Same as R1 |
| SOL floor | 10 SOL (safety; R ignores it, but execution still needs ticket ≤ 5% of SOL side, so a 0.5 SOL ticket implies ≥ 10 SOL) | Safety ignored; execution ticket-share still binds | Execution binds | ≥ 50 SOL | ≥ 50 SOL |
| Entry rule | Every lead that passes all safety + execution, subject to MAX_OPEN 4 and 8 F entries/day | Random sample from the same runs, about F's daily count, cap 2× F, execution only | Fails safety, passes execution, cap as R | One mint drawn with p = 1/6 from `U_t`, max 10/day | **Same draw** as R1, then R2 filters |
| Top-10 | < 35% ex pool/curve/locker (F) | Ignored | Typically fails this or another safety | Not a filter (honesty does not use it) | < 30% ex pool/curve/locker |
| Dev | ≤ 10% (F) | Ignored | May fail | Not a filter | ≤ 10% |
| Sells in last hour | Execution, all books | Yes | Yes | Not required (only a sell-quote route at entry) | ≥ 1 sell in `txns.h1` |
| Authorities | Open = safety skip (F). R may enter open-authority tokens | Ignored | May fail | Required revoked (honesty (c), all race wallets) | Required revoked |
| RugCheck score | ≤ 40 (F) | Ignored | May fail | Not a filter | Not a filter |
| LP lock | 90% by pool type (F) | Ignored | May fail | Not a filter | Not a filter |
| Rugged / danger flags | Safety skip (F) | Ignored | May fail | Not a filter | Not a filter |
| Sell-side impact ≤ 3% | Execution, all books | Yes | Yes | Not a hard filter (cost uses verified impact; no 3% skip) | Same as R1 |
| Ticket ≤ 5% of SOL side | Execution, all books | Yes | Yes | Implied in practice by the 50 SOL floor + 0.5 SOL ticket (1%), but not a separate skip | Same |
| Ticket | 0.5 SOL | 0.5 SOL | 0.5 SOL | 0.5 SOL | 0.5 SOL |
| Cost model | 60 + 0.25×CP + 150 on stress exits, cap 600 | Same | Same | Same formula; 150 bps only on R2's −20% stop (R1 has no stop); no extra network/priority fee | Same |
| Hard stop | −25% | −25% | −25% | **None** | **−20%** |
| Take-profit | Ladder 1/3 at +40%, 1/3 at +100% | Same | Same | **None** | **Full exit at +25%** (no ladder) |
| Trailing stop | 25% after +40% | Same | Same | None | None |
| Liquidity exit | SOL side down 40% | Same | Same | None | None |
| Time stop | 120 min unless up 10% | Same | Same | **Fixed 4h, no "unless up" clause** | **Fixed 4h, no "unless up" clause** |
| Death | 3 no-route or pool gone = 0 / −100% | Same | Same | Same idea (rule (b)) | Same |
| Cadence | 10–30 min (Actions 30 min) | Same | Same | :12 and :42 only (30 min) | Same as R1 |
| Daily cap | F: 8 new entries; MAX_OPEN 4 | About F's count, cap 2× F | Same cap as R | 10 fills | 10 fills |
| Holder source | RugCheck | — | — | Public RPC (authorities only) | Public RPC (holders, dev, authorities); no RugCheck |

### 13.2 Labels

**R1: close variant of v6.5 R**, not of F or S.

Reasons to call it a close variant of R: it is the race's random fresh-meme baseline; it uses the same 0.5 SOL ticket and the same cost skeleton; it trades the same *kind* of pool (young Solana memes); dead inventory goes to 0; Challenger asked that close matches be labelled this way because results can rough-proxy v6.5-style trades.

Reasons it is not the same book: the universe is launchpad graduates aged 1–6h with a 50 SOL floor, not the DexScreener paid feed aged 15 min–48h with a 10 SOL safety floor; R1 cannot enter ungraduated bonding-curve tokens, while v6.5 R can (it ignores the `not_graduated` safety filter); R1 requires revoked authorities (v6.5 R does not) and does not require 1 sell in the last hour or the 3% impact / ticket-share execution skips (v6.5 R does); the draw is an independent p = 1/6 per slot, not a time-matched sample sized to F; **exits are distinct** — a single 4h time stop and death, versus the full v6.5 −25% / +40% / +100% / 25% trail / 40% liq-drop / 120-min-unless-+10% ladder. Do not read an R1 lead or lag as a verdict on v6.5 R.

**R2: close variant of v6.5 F**, not of R or S.

Reasons to call it a close variant of F: it is the race's filtered fresh-meme book; it keeps the spirit of F's safety screen (graduated, authorities revoked, dev ≤ 10%, a concentration cap, activity) plus a discretionary exit; same ticket and cost skeleton; same kind of pool.

Reasons it is not the same book: universe, age window and reserve floor differ as for R1; top-10 is 30% not 35%; R2 has no RugCheck score cap, no LP-lock rule, and no rugged/danger skip; R2 does not use the 3% impact execution skip; R2 enters only the shared random draw, not every name that passes the filters; holder data come from public RPC, not RugCheck; **exits are distinct** — full take at +25%, stop at −20%, fixed 4h time stop, no ladder, no trail, no liquidity exit, versus the v6.5 ladder above.

R2 is **not** a close variant of v6.5 S (S is the book of safety-rule failures).

**Note, not a v6.5 label:** R2's filter set (graduated, ≥ 50 SOL, top-10 < 30%, dev ≤ 10%, ≥ 1 sell in h1, authorities revoked) is closer in *thresholds* to v2 S2 than to v6.5 F, but the age window is the opposite of S2 (R2 is 1–6h; S2 is ≥ 6h) and the locked exits are Challenger's +25% / −20% / 4h, not S2's −35% and 24h. S2 is not a v6.5 F/R/S book and is not used as a board label.

**Board wording, once the race is live:**

- R1: `close variant of v6.5 R`
- R2: `close variant of v6.5 F`

under the standing banner that a lead is luck until tested, and under the rule that these labels must never appear in a v6.5 report or a 25 Nov decision.

---

## 14. Choices made at lock

These are conservative fill-ins for items the Challenger spec left open. They are locked with this file.

1. **Honesty rule (e).** Challenger named rules (a)–(e) but listed four fill bullets. (e) is the temporary-error / cap rule: skip new entries, keep a stale last mark, retry, and do not count toward the 3-in-a-row death streak.
2. **Network and priority fees.** Not added. Challenger's cost line is 60 bps + 0.25 × impact + 150 bps on stop exits, capped at 600 bps, and that is what is used. The v2 desk extras (0.00001 SOL + 0.001 SOL) are not part of this race.
3. **Impact definition.** Verified constant-product impact `10000 * Δx / (x + Δx)` in bps, not Jupiter `priceImpactPct`. Missing reserve → 600 bps cap on that side, logged.
4. **Stop-exit definition.** Only R2's −20% hard stop takes the +150 bps. Time stops, take-profits, rebalances, R4's 24h exit, and death-at-0 do not.
5. **Launchpad endpoints and dex ids.** Closed set and URLs in §5. Eligible: `pumpswap` and `letsbonk-fun`. Not eligible: `pump-fun` and every other dex, including `raydium-launchlab` (a different launchpad, not labelled here as letsbonk's curve). Only WSOL-quoted pools. USD1-quoted or Bonk-quoted letsbonk pools are out. Per-dex `new_pools` is 404 and is not used.
6. **SOL reserve source order.** DexScreener WSOL-side liquidity, else public RPC vault balance, else skip. No USD conversion.
7. **Discovery pagination caps.** At most 6 GT pages per graduated dex plus 2 network `new_pools` pages per run, paced for the public ~10 calls/min limit and the 5-minute timeout. Coverage is logged. The top-pools lists are biased toward larger names; that shortfall is accepted and logged rather than opening a paid API.
8. **Holder / dev / authority source.** Public RPC, as Challenger specified. RugCheck is not used (it is the v6.5 F holder source). If a holder or dev read fails, including a 429, R2 skips that mint and logs the reason.
9. **Top-10 exclusions.** Exclude pool, bonding-curve and identified AMM-program accounts, as specified in §8, under R2's 30% cap. Unidentified lockers are not excluded (tighter filter).
10. **Seeding formula.** Exact bytes in §6 (`SHA-256` of `{RUN_SLOT_ISO}|R1R2`, first 8 bytes, `random.Random`, `randrange(6) == 0`, then `randrange(len(U_t))` on the sorted mint list).
11. **Paired draw.** One shared draw from pre-filter `U_t`; R2 keeps the same mint only if its filters pass. Chosen so "the same random draw rule" actually makes R2 vs R1 a fair look at filters and exits. No redraw on skip.
12. **No re-entry.** Each race wallet enters a mint at most once. Already-entered or still-open mints that are drawn again are skipped, not replaced.
13. **MAX_OPEN.** No extra open-position cap beyond the 10 fills/UTC-day cap and the one-new-entry-per-slot rule. Jupiter use prefers marks over new entries when a sub-cap is tight.
14. **Official rank unit.** Net % in SOL, so the SOL hold is 0.00%. USDC is a companion column only. No scaling of windows shorter than 30 days.
15. **95% interval method.** Day-clustered bootstrap, 10,000, percentile, seed `20261111`, mean per-trade net % in SOL, `n/a` below 5 closes.
16. **R3 eligibility sources and conservatism.** CoinGecko `meme-token` + proven Binance *spot* listing date `<= 2025-11-11`. Listing dates may be proved from Binance klines or, on 451, from first-party `data.binance.vision`. If the date still cannot be proved, the name is excluded. The top 5 are resolved at the first successful list-build run, not in this file. Shortfall handling: take fewer than 5 if that is all that qualifies; take zero and turn R3 off if none do. Liquidity rank and monthly rebalance timing are in §9. First-run size is 0.5 SOL per name. Eligibility is frozen as of 11 Nov 2026.
17. **R4 announcement clock and titles.** `first_listed_at`, not `listed_at`. Closed first-listing and exclusion title patterns are in §10. Ambiguous mint or uncheckable "first listing" status → skip. One open position; queue in announcement order. Exit 24h after entry, not after `T_ann`. Reachability failure on the first 2 runs turns R4 off for the race.
18. **Per-wallet Jupiter sub-caps.** 14 / 14 / 7 / 3 / 2, summing to 40, with no reserved fifth-wallet slice, as in §4.
19. **Missed slots.** No catch-up fills. Late runs keep the scheduled `RUN_SLOT`.
20. **Privacy and wording.** Codenames, "the owner" only, privacy guard unedited.
21. **Scope.** This lock is R1–R4 plus the SOL hold only, matching the owner's 12:14 PM yes. A later fifth wallet is out of scope.

---

## 15. Lock line

LOCKED at the timestamp recorded in `prereg_races.lock`. The SHA-256 in that file is of this exact `prereg_races.md`. After lock, any change to an entry, exit, cost, cap, seed or universe rule starts a new, separately labelled book and a new hash.

The owner's yes to merge remains a separate step after Challenger's gate on the PR (exact head, CI green, first 2 runs green with call counts, privacy guard).
