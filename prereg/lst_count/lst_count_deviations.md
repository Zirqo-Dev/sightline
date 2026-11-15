# LST count deviations

Append-only. `lst_count_spec_v1.md` stays locked and is not edited. New lines are added at the end of this file. Written Nov 11, 2026, before any capture.

## Interpretations (Nov 11, 2026, before any capture)

- mSOL fee = 0. Display only. Raw D is unchanged.
- jSOL is the JPool JSOL mint 7Q2afV64in6N6SeZsAAB81TJzwDoD6zpqmHkzi9Dcavn.
- Re-arm is exact 72h (`hour_start >= onset hour + 72h`), and that hour can onset.
- Median resting D is the median D on captured hours with D < 30 bps.
- Extra MISSING reasons (`pool_mint_mismatch`, `rate_undecodable`) are finer splits of `rpc_fail`.
- An onset's UTC day is the slot hour's UTC date.

## Deviation (Nov 11, 2026, before any capture)

- Dropping "plus any other mint" discovery. The candidate set is the 10 named LSTs. That is stricter for a PASS.

## Process rule (Nov 11, 2026, before any capture)

Until T0+336h, no bot reads the onset or d_bps fields in captures.jsonl. The heartbeat and the weekly review read status.json only.

§1b addendum (owner approved Nov 11, 2026 3:21 PM EST, before any capture): liquidity = sum of reserve_in_usd over all page-1 LST/SOL and LST/WSOL pools >= $5,000,000; each pool counted is logged.

§8 addendum (owner approved Nov 14, 2026, 7:10 PM ET, committed before any external dispatch): An hourly workflow_dispatch of lst-count.yml on ref main from cron-job.org at :17 UTC is added as a capture trigger alongside the GitHub schedule. It takes effect from the first such run after this commit; that run's id and UTC time are appended below as a dated line. Added after T0 because of coverage only: status.json (written 2026-11-14T21:32:44Z) showed 1 captured slot; as of 2026-11-15T00:12Z, 2 more slots (22Z and 23Z) had ended with no capture. No bot read onset or d_bps fields before this decision. The owner confirmed on Nov 14, 2026 that the 2:51 PM HOLD did not cover GitHub scheduled captures. Unchanged: the analysis rules, the frozen set (JitoSOL only, reserve $6,412,716 at the freeze, recorded in the status.json written 2026-11-14T21:32:44Z on lst-count-data commit d4823fb513897e799c49b11e96fe673720bc4e78, record sha256 c439986449168f8d71146ec0183f07964a6b6e0f9fc3ffdb266a2ac93981fb50), T0 2026-11-14T21:00:00Z and window_end 2026-11-28T21:00:00Z. The gate is not loosened and no tokens are added. The per-capture trigger source comes from Actions run event metadata. The capture minute may vary within the hour, and has_record keeps one record per token per slot. Gaps stay no_run and are never backfilled. The coverage, BLOCKED and PASS/KILL rules in §6-§7 are unchanged. The heartbeat counts only schedule events, so it showing lst-count red is not a fault.

§8 first external run (dated Nov 14, 2026 ET): the first workflow_dispatch of lst-count.yml from cron-job.org after the §8 addendum commit (4c1b86338e7d6723b6acc64d4580b939e5c2ce95) was run 43661704155, created 2026-11-15T01:09:13Z, on ref main at 4c1b863. It was the owner's manual test call from the cron-job.org job and captured slot 01:00Z. The first scheduled cron-job.org call was run 43662357838 at 2026-11-15T01:17:05Z; has_record skipped the already-filled slot. External trigger effective from 2026-11-15T01:09:13Z.
