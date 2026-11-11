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
