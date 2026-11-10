# NFT snapshot pilot — NH-04 spec v0.3

Status: approved by Sightline Challenger as Option A with required revisions (Nov 10, 2026); this revision applies them, plus the HOLD fix round on PR #116 at b457e09 (fixes 1–5, section 13) the re-gate items at 3666893 (section 14), and the owner's decisions of Nov 10, 7:47 AM EST (section 15). Implemented in the v0.3 PR (not merged; merge only after Challenger PASS on the 8-item checklist at the exact PR head). The owner is never named; no collection identifiers appear here.

## 0. Why v0.3

- NH-04 v0.2 is **STOPPED** (early kill, addendum 2). Not GO, not NO-GO: the cost question on sub-$250 collections is unanswered.
- First 3 ok runs on 1a536e8 averaged 1.33 floor-passing collections against 5 (1, 1, 2): 4 pass events, 2 distinct collections.
- The bottleneck is the 24h liquidity screen, not universe depth.

## 1. Evidence: read-only offline replay (Nov 10, 6:55–6:59 AM EST, one snapshot)

Re-ran the frozen v0.2 universe step (market_cap sort, ethereum+base, 40 under-cap collections) with a separate free agent key (now **retired**: see section 15), then read detail, offers and 7 days of sale events for each of the 40. 229 OpenSea calls (107 universe + 122 per collection). No writes.

| Option | Window | sales | buyers | top buyer | full pass of 40 |
|---|---|---|---|---|---|
| v0.2 control | 24h | ≥10 | ≥6 | ≤20% | 2 (live run had 2) |
| (a) chosen | 7d | ≥30 | ≥10 | ≤20% | 8 |
| (b) halved | 24h | ≥5 | ≥3 | ≤20% | 2 |
| (b2) halved, cap 40% | 24h | ≥5 | ≥3 | ≤40% | 6 |

Sensitivity of (a): top buyer ≤25% → 10; ≤15% → 7; 20/8 → 9; 40/12 → 6; 70/20 → 2.

## 2. Frozen rule (item 1)

- **Liquidity screen over the trailing 7 days: ≥30 sales, ≥10 distinct buyers, no buyer above 20% of 7-day sales.** Unchanged: ≥3 bidders within 85% of the top collection bid, ≥5 live listings, floor ≤ $250, round-trip gas < 2% of floor, ERC-721, one non-shared contract, mint ≥14 days, not disabled.
- **These thresholds were chosen after seeing one replay snapshot (Nov 10) and are frozen.** Addendum 3 forbids any change during the test; a change is v0.4 with a fresh clock and the full gate.
- They are frozen in the rule string written to every status row and unit before the first v0.3 run:
  `v0.3 GET /api/v2/collections order_by=market_cap limit=100 max_list_pages=8 pass_target=10 max_calls=480 chains=ethereum,base pace_per_minute=60 window=7d min_sales=30 min_buyers=10 max_buyer_share=0.20 min_bidders=3 bid_band=0.85 min_listings=5 floor_usd=250 gas_ratio=0.02 min_mint_days=14 event_pages=3 truncated=flag`
  - Code: `nft/loop/pricing.py` (`WINDOW_DAYS`, `MIN_SALES`, `MIN_BUYERS`, `MAX_BUYER_SHARE`, `RULE`). Test: `test_rule_string_freezes_v03_thresholds`.
- **Screen until 10 passing:** page by market_cap (8 pages per chain, in the rule string) and screen each under-cap collection immediately; stop at 10 passes, the 480-call cap, the 11-minute wall clock, or the end of the page depth.
- **Measured vs partial (fix 1):** once every pending row has been served, a screen that stops for any of those reasons is a **measured run**. It records `floor_passing_this_run` = passes found, appends to `first_runs`, records the day's passes, and may set `pilot_start`. `status.stop` = `pass_target` | `depth` | `call_cap` | `deadline`. A run is `partial` only for a list error, deferred pending rows, no ETH price, or a gas failure, and then `stop` is null. `status.by_chain` reports screened and passing counts per chain. Code: `snapshot._screen`, `snapshot.run`. Tests: `test_screen_until_pass_target`, `test_capped_run_with_four_passes_is_measured`, `test_depth_stop_is_measured`, `test_wall_clock_stop_is_measured`.
- Units from any other rule string (v0.2 and older) never count toward v0.3 (`summarize` filters on the rule string).

## 3. Rate limit and pacing (item 0)

- Repo key: run 42798395767 `ran_at` 10:21:16Z; `x-ratelimit-reset` 1794306390 = 10:26:30Z. The run took about 4.5 min (last call about 10:25:30Z), so the reset sat about 60 s after the last call and 5 min 14 s after the start. The limit of 120 is therefore a **rolling per-minute** window, not hourly. (The earlier 600/h reading came from a separate free agent key with an hourly window.)
- Pacing: 60 reads/min (one read per second, half the 120/min shared bucket), unchanged in code and now asserted.
  - ~340 calls ÷ 60/min = 5.7 min; the 480-call cap ÷ 60/min = 8.0 min. Both are inside the 11-minute run limit (15-minute job timeout).
  - At 60/min the job never uses more than 50% of any one-minute window, leaving 60/min for the account's other keys. The low-bucket reserve wait (≤24 remaining) is capped at 70 s, which covers a 60 s window.
- Code: `pricing.py` (`READS_PER_MINUTE`, `PACE_PER_MINUTE`, `MAX_CALLS`), `opensea.py` (`_wait_seconds`). Test: `test_pace_fits_the_per_minute_window`.

## 4. Early kill: two gates (item 2)

- (i) **First 3 ok runs average fewer than 6 passing → stop.** Status `early_kill.result = "STOPPED"`.
  Capped, depth and deadline runs count (fix 1). Test: `test_three_capped_runs_under_six_fire_gate1`.
- (ii) **72 hours after the first measured run, fewer than 8 distinct collections passing in measured runs inside that 72h → stop, result INCONCLUSIVE** (fix 2; a 72h window, not UTC days 1–3). books.json keeps `first_measured_at` and `early_distinct`. Test: `test_gate2_counts_only_passes_inside_72h`.
- **Calendar backstop for the pace rule (fix 4):** the v0.3 anchor `v03_init_at` is the first v0.3 run after merge/deploy (state init time). If 7 days after the anchor there are fewer than 4 UTC days with a measured run, stop (`early_kill.backstop = fire`, result STOPPED), even if `pilot_start` was never set. books.json keeps `measured_days`. Test: `test_calendar_backstop_fires_without_pilot_start`.
- The code reports all three in every status row (`early_kill`: gate1/gate2/backstop = pending/pass/fire, run counts, distinct count, measured days, result). Stopping is the human decision, as for v0.2. Code: `pricing.early_kill`. Test: `test_early_kill_two_gates`.

## 5. GO definition and F2 (item 3)

- F2 (median ≥20 floor-passing a day over days 8–14) is **demoted to a reported metric** (`pace_median`, `pace_ready`, plus `distinct_per_day_median` over days 1–14). It is no longer a gate.
- NH-04 verdict (`nh04_verdict` in status):
  - `not_ready` before day 14 completes;
  - `INCONCLUSIVE` if fewer than 20 **numeric-cost** units (null-cost units never count toward 20; fix 3), fewer than 8 distinct collections (addendum 1), or null-cost share above 20%;
  - `NO-GO` if the median cost is above 10%;
  - otherwise **`cost GO, supply-limited (median N distinct/day)`**, never plain GO. The schema enforces this wording.
- Code: `pricing.summarize`. Tests: `test_go_is_cost_go_supply_limited_and_footnoted`, `test_min_units_counts_numeric_costs_only`.

## 6. New reported fields, not gates (item 4)

- **7-day top-seller share** (`counts.top_seller_share_7d`).
- **Largest single-day share of 7-day sales** (`counts.max_day_share_7d`: the largest of the seven 24h buckets ending at the snapshot).
- Both are stored on each passer's pending row and its unit. Status reports `passers_footnoted` per run. If any unit in the window has either share above 50%, `verdict_footnote` is set ("k of n units: top seller or one day above 50% of 7d sales") and the verdict must carry that footnote.
- Code: `pricing.sale_profile`, `pricing.footnoted`, `summarize`. Tests: `test_sale_profile_reports_seller_and_single_day_share`, `test_go_is_cost_go_supply_limited_and_footnoted`.

## 7. Pending-unit budget (item 5)

**Order:** pending re-checks are served **before** any new screening. A re-check costs listings 1 + collection 1 + offers 1–3 = **3–5 reads**. Listings and offers are cached, so a pending collection met again in the universe costs only its event pages. Tests: `test_pending_rechecks_are_served_before_screening`, `test_call_cap_during_pending_defers_and_is_partial`.

**Pending size bound:** at most 10 new passers per run (PASS_TARGET). A pending row that can't be read (error or call cap) is retried; after 3 tries it closes as a null-cost unit (reason `read_error` or `budget`). So at most 10 fresh + 10 on try 2 + 10 on try 3 = **30 pending** enter any run. Day 14 is the same steady state; pending doesn't accumulate across days.

**Day-14 worst case per run:**

| Part | Typical (replay rates) | Worst case |
|---|---|---|
| Pending re-checks | 10 × 3 = 30 | 30 × 5 = 150 |
| Universe (list pages + listings, ~2.7 per under-cap collection) | 50 under-cap (10 passes at the replay's ~20%) × 2.7 ≈ 134 | up to 16 list pages + 1 listings (+1 contract) per row, unbounded by passes |
| Screening (collection 1 + offers 1–3 + events 1–3) | 50 × ~3.05 ≈ 153 | 50 × 7 = 350 |
| **Total** | **≈ 317** (5.3 min at 60/min) | **150 + 134 + 350 = 634 > 480** |

**When demand exceeds 480:** the client stops at 480 (`Budget`).
- Pending is served first, so fills are never starved. The worst-case 150 leaves at least 330 reads for screening, about 34 under-cap collections at the 9.7-read worst case.
- If the cap stops screening before 10 passes, the run is still **measured** (fix 1): `stop = call_cap`, passes found are recorded and count toward `first_runs`, gate (ii) and the day. Only when the cap is hit while **pending rows are still being served** (deferred pending) is the run partial (`reason = "pending deferred"`, `stop` null), with no day recorded and no new screening.
- Passers found before the cap still become pending (same as v0.2). Unreached pending rows stay pending with tries + 1.
- The status reports `opensea_calls`, `screened`, `by_chain`, `pending_served`, `pending_deferred` and `stop`.

## 8. Truncation (item 6)

- More than 300 sales in 7 days (3 event pages of 100 with a next cursor): **flag, don't reject.**
- Buyers and top-buyer share are computed on the 300-sale sample. Sales passes (≥300).
- Status `flags.sales_truncated` counts every truncated screen. `flags.liquid_tail` counts truncated passers, and the passer's counts carry `sales_truncated: true, liquid_tail: true`. `sales_truncated` is no longer a rejection reason.
- **Caveat (fix 5):** on a truncated collection, every share (top buyer, top seller, max single-day share) is computed on the **newest 300 sales**. That sample covers less than 7 days, so `max_day_share_7d` is overstated (and seller and buyer shares are shares of the sample). These rows are flagged (`sales_truncated`, `liquid_tail`), and the >50% verdict footnote may over-fire for them.
- Code: `pricing.rejection`, `snapshot._screen_one`, schema `flags`. Test: `test_truncated_sales_are_flagged_not_rejected`.

## 9. Clock, state, workflow

- `STATE_VERSION = "v0.3"`. On load, any other version clears pilot_start, complete_days, passes, first_runs and v0.2 pending rows (v0.2 passers weren't screened on the v0.3 rule). Gas samples, seen days and offer books stay.
- **Fresh 14-day clock** from the first status=ok v0.3 run. v0.2 runs are "v0.2 observation, no verdict" and never count.
- `.github/workflows/nft-snapshot.yml`: **GitHub `schedule` cron removed**; `workflow_dispatch` kept. The owner's cron-job.org job (hourly at :27) is the trigger of record, so two runs can't land in one hour. Own concurrency group `nft-snapshot` unchanged. Test: `test_workflow_is_isolated`.
- Touches no Solana file, rule or workflow, and nothing the frozen v6.5 test reads or reports. NFT files only: `nft/loop/*`, `schema/nft-snapshot.schema.json` (read only by the NFT tests), `.github/workflows/nft-snapshot.yml`. No new recurring costs.

## 10. Addenda carried forward

1. **Minimum sample:** by day 14, at least 20 numeric-cost units across at least 8 distinct collections; otherwise INCONCLUSIVE, never GO.
2. **Early kill:** replaced by the two gates and the calendar backstop in section 4.
3. **No loosening mid-test:** no change to window, thresholds, universe or pass target during the 14 days.
4. **Reporting:** per-run rejection counts, flags and floor_passing; scheduled vs actual runs per day (cron-job.org hourly at :27 is scheduled; actual = status rows on nft-data).

Carried from v0.1/v0.2: cost = (entry − exit) ÷ entry with t+1 2nd-cheapest listing and held bid; median over all units days 1–14; no-bid units cost 100%; null-cost share ≤20%; seasoned subset reported separately.

## 11. Bias statement

- The 7-day window and screening deeper into the market_cap ranking admit thinner collections than the 24h screen (replay passers had as few as 2 sales in the last 24h). Thinner books mean wider spreads and more no-held-bid units, so measured cost goes **up**, leaning toward **NO-GO**. That's the conservative direction for a GO, and it's accepted as stated.
- market_cap sort favours high-supply collections, which may understate cost (v0.2 bias, kept).
- Thresholds were picked after one replay snapshot. Hourly snapshots are correlated, and 8 on one read doesn't prove a steady 8.
- Gate (ii) and the GO wording make a supply-limited result explicit rather than hidden.

## 12. Owner-only, not proposed

Raising the $250 floor cap is the owner's money rule. This spec doesn't propose it. If v0.3 stops early, the next step is a spec decision for the owner (end NH-04 as untestable at this cap, change the source or chains, or the owner revisits the cap), not more code.

## 13. HOLD fix round (Challenger, PR #116 at b457e09)

1. Measured runs include call_cap, depth and deadline stops after all pending rows are served; `status.stop` added; partial only for list errors, deferred pending, no ETH price, or gas failure (sections 2, 7).
2. Gate (ii) is a 72h window from the first measured run (section 4).
3. Addendum 1 counts numeric-cost units only (section 5).
4. Calendar backstop: fewer than 4 measured days 7 days after the v0.3 anchor → stop, even with no pilot_start (section 4).
5. Truncation caveat: shares on the newest 300 sales overstate max_day_share; flagged; footnote may over-fire (section 8).

## 14. Dry run, merge order (Challenger re-gate at 3666893)

**DRY_RUN input (A).** `nft-snapshot.yml` has a boolean `workflow_dispatch` input `dry_run`, default **false**.
- With `dry_run = true`, the job runs `snapshot.py --dry-run` against a fresh `mktemp -d` directory and writes only the job step summary: status, stop, opensea_calls, floor_passing_this_run, by_chain, rejections. These are counts only, with no slugs or ids.
- The privacy-guard and "Commit to nft-data" steps are skipped (`if: success() && inputs.dry_run != true`), so `publish_nft_data.sh` never runs. Code-level guards back this up:
  - The publish script exits 1 when `NFT_DRY_RUN` is not `false` (the job sets it from the input).
  - `--dry-run` refuses any data dir that isn't an empty directory outside a git checkout.
- cron-job.org dispatches send only `{"ref":"main"}`, so `dry_run` takes its default (false) and those runs publish as normal.
- Code: `.github/workflows/nft-snapshot.yml`, `nft/loop/publish_nft_data.sh`, `snapshot._dry_dir_ok`, `snapshot.write_summary`, `snapshot.main`. Tests: `test_dry_run_never_publishes`, `test_dry_run_summary_is_counts_only`.

**Deadline during the pending serve (B).** If the wall clock is reached while pending rows are being served, the run is partial: `stop` is null, `floor_passing_this_run` is null, the deferred rows get tries + 1, and neither `first_runs` nor `measured_days` is appended. Test: `test_deadline_during_pending_serve_is_partial`.

**early_distinct (C).** It holds collection-level ids (`nft_collection:eip155:<chain>:<contract>`, one per contract), the same ids as `passes` and `pending`. It never holds per-token ids, and re-passes don't grow it. No fix was needed. Test: `test_early_distinct_holds_collection_ids`.

**Dry-run procedure (D).** No OpenSea calls on any key before merge; the spare agent key is retired from use.
1. After merge, the owner dispatches `nft-snapshot` **once with DRY_RUN = true** on the repo key, at least 10 minutes away from :27 (for example, between :40 and :15).
2. Publishing v0.3 runs resume only after that dry run is green and its summary has been read: stop reason, calls used, passes, and screened and passing counts per chain (including Base).
3. Checklist item 7 ("next 2 runs green") counts from the dry run.

**Merge-order rule.** No publishing v0.3 run may happen until either:
- the owner pauses his cron-job.org job before merge (and re-enables it after the dry run), or
- the summary-only books.json change and the Chainlink change land first.
Otherwise the first hourly dispatch after merge would publish a v0.3 run before the dry run.

## 15. Owner decisions (Nov 10, 2026, 7:47 AM EST) and what changed

The owner's words, quoted:
- "A: yes, I'll pause the cron-job.org NFT job"
- "A: publish OpenSea summary only, don't rewrite history"
- "A: switch to Chainlink and drop Coinbase"
- "A: use one OpenSea key only"

**1. Summary-only publishing.** books.json and every other nft-data file no longer carry per-offer prices or raw order hashes.
- The held-bid check persists only `book = {digests: SHA-256 of each order hash, top_bid_eth, bidders_near_top}`, for both pending rows and the offers map.
- Next run, a current bid counts as held if the SHA-256 of its hash is in the previous digests. The current price is read live and never stored per order.
- On load, any legacy list-form offer book or pending row with a raw book is dropped.
- nft-data history is **not** rewritten and nothing is force-pushed; earlier commits keep what they had.
- The attribution label changed from "Derived metrics only" to: "Published as summaries only: aggregates, computed costs, and SHA-256 digests of order hashes; no per-order prices or raw order data. USD from the Chainlink ETH/USD feed only."
- Code: `pricing.order_digest`, `pricing.book_summary`, `pricing.held_bid`, `snapshot.load_state`. Tests: `test_published_files_have_no_raw_offer_fields`, `test_legacy_raw_offer_books_are_dropped_on_load`, `test_t1_fill_held_bid_and_no_wallet_leak`.

**2. ETH/USD from Chainlink; Coinbase removed.**
- Primary: the Chainlink ETH/USD aggregator on Ethereum (`0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419`), read with `eth_call` of `latestRoundData()` (`0xfeaf968c`) over the existing keyless Ethereum RPC. No key and no new dependency.
  - The answer has 8 decimals. It's rejected if updatedAt is more than 1 hour old, more than 5 min in the future, non-positive, or answeredInRound < roundId.
  - Live check on Nov 10 (morning, EST): "ETH / USD", 8 decimals, about $2,090, updated about 30 min earlier.
- Fallback: DefiLlama, **internal only**. Its price can screen the $250 cap, but it's never published: status `eth_usd` is null, `eth_usd_source = "fallback"`, and new pending rows get `floor_usd = null`.
- Published files carry ETH values plus Chainlink-derived USD only (`eth_usd_source = "chainlink"`).
- Note: the feed's heartbeat is 1 hour, so a quote just over 1 hour old can push a run onto the unpublished fallback. That's expected and not an error.
- Code: `snapshot.parse_chainlink`, `snapshot.fetch_eth_usd`, `snapshot.run`. Tests: `test_chainlink_primary_and_stale_rejected`, `test_defillama_price_is_never_published`, `test_coinbase_is_gone`, `test_eth_usd_parser_keeps_unknown_null`.

**3. One OpenSea key only.** The spare free agent key on the box is **retired and must never be used again**. OpenSea API Terms 7.1 bar circumventing limits, including by creating multiple API keys. The only key is the repo secret `OPENSEA_API_KEY`. The replay (section 1) and the interrupted offline dry-run attempt earlier on Nov 10 (stopped by the owner before it finished, no results) were the last uses. The pre-merge dry run is replaced by the post-merge DRY_RUN dispatch on the repo key (section 14).

**4. Merge order.** The owner pauses the cron-job.org NFT job before merge (his decision A), which satisfies the merge-order rule in section 14. He dispatches the DRY_RUN on the repo key and re-enables the job only after the dry run is green.

**5. Chainlink terms (required before merge).**
- Chainlink ToS v6.0, effective Aug 18, 2026 (https://chain.link/terms), §3 licenses access through designated public interfaces. Our use is an on-chain `latestRoundData()` read over a public RPC.
- No clause bans republishing a feed value or a USD figure derived from it.
- No attribution is required. The trademark terms only bar implying endorsement, and nothing here does.
- Residual unknown: §2(iii) defers to third-party data-provider terms, which aren't published per feed.
- §2(iv) and https://docs.chain.link/data-feeds/developer-responsibilities put data quality on the consumer. That's why there's an updatedAt ≤ 1h staleness check, plus the round and sign checks in `parse_chainlink`.
- The full note is on SPD-123 (Chainlink terms note).

**6. Q1: quiet-hour fallback (Challenger, Nov 10).** The Chainlink heartbeat is 3600 s, so quiet hours often land at 3600–3700 s and use the internal DefiLlama fallback. That doesn't bias the verdict, so `CHAINLINK_MAX_AGE` stays at 3600 s:
- **Unit cost is ETH-only.** `pricing.round_trip` computes `cost = (entry − exit) / entry` from ETH listing, bid, fees and gas (pricing.py lines 328–343). `snapshot._unit` takes the cost straight from it (snapshot.py lines 214 and 221). No USD value enters, so a fallback run can't create null-cost units, and MIN_UNITS / null-share (pricing.py lines 552–556) see the same units either way.
- **The $250 cap uses the internal price.**
  - `detail.usd = eth_usd` holds whichever price was fetched, fallback included (snapshot.py lines 615 and 618).
  - `floor_usd = floor * detail.usd` is computed per collection (line 459) and checked by `_under_cap` (lines 466 and 520–521).
  - The fallback only blanks what's published, after screening: pending `floor_usd` (lines 634–638) and status `eth_usd` (line 693).
- **Source counts.** Measured runs are counted by source in `books.json usd_runs` and reported as `status.eth_usd_runs {chainlink, fallback}`. When any measured run used the fallback, `verdict_footnote` adds "ETH/USD source on measured runs: N Chainlink, M fallback (fallback USD not published; costs are ETH-only)". No-price partial runs aren't counted.
- Tests: `test_q1_fallback_runs_keep_numeric_costs_and_cap` (quote at 3700 s → fallback; 2 fallback runs give 4 units with numeric cost, 0 null; cap still rejects at $3,000/ETH) and `test_usd_source_counts_mixed`.

## 16. Outage runs are not a supply result

A dead OpenSea key used to write `status.json` (`error` / `auth`, or `no_key`) and leave the job green. Those runs are not measured, so a long outage can make the 7-day backstop fire STOPPED, which reads as a supply result.

- The status is still written and committed first. The workflow step "Fail the job on a published outage" then runs with `if: always() && inputs.dry_run != true`, reads `nft-pilot/status.json`, and exits 1 when the file is missing, status is `error` or `no_key`, or status is `partial` with reason `opensea http 401` or `opensea http 403`. A partial `opensea http 429`, a 5xx, and `no eth price` do not fail the job. A dry run skips the step. The snapshot job's `if` is `github.event_name != 'pull_request'`, so this step does not run on a pull request. No new secret.
- `status.outage_runs` is the count of every non-measured run since `v03_init_at`: `error`, `no_key`, auth (401/403), a partial `opensea http 429` or 5xx from `list_collections`, a `no eth price` partial, and any other partial that did not measure supply. They are not measured days. An outage before the anchor is on disk is not counted, so a missing key cannot start the backstop clock. `books.json` `outage_at` stores each row as `t`, `early` (measured runs already saved), and `reason` (the footnote label: `auth`, `http 429`, `no eth price`, `no_key`, ...). `early_kill` does not read that list. `status.outage_runs` stays an integer, so the status schema shape is unchanged.
- When gate (i) or the 7-day backstop fires and that window contains any of those runs, `verdict_footnote` adds "N non-measured runs in window (auth 2, http 429 3, no eth price 1); stop may reflect data outage, not supply", with the counts for whichever reasons are in the window, sorted by label. Gate (i)'s window is from the first measured run until the third measured run fills the sample (`early` still under 3). The backstop window is the 7 days from `v03_init_at`. A run in both counts once. The kill label stays `STOPPED`. Thresholds are unchanged. Gate (ii) does not add this note.
- A generic exception in `main` returns 1 (`status` `error`, reason `error`). The commit step is `if: success()`, so that run is not pushed and its outage row is not published. That is accepted: the job is already red.
- Tests: `test_opensea_401_publishes_then_fails`, `test_no_key_fails_the_published_run`, `test_outage_footnote_only_when_the_window_has_outages`, `test_http_429_partials_are_footnoted_and_stay_green`, `test_healthy_run_stays_green`.
