# Random twin check, 2026-11-02

Read-only check of books F, R, and S. Nothing here changes an entry, an exit, a cost, a sampler, the frozen v6.5 config, or a running workflow.

Source: desk-data `solana-desk/trades.csv` at `bbe953651ffea0da0006d7e9a86cd6302352d179` (sha256 `5c9aa46424acfd6c95047f43fd4cc9cc3f32844d96c1ab218f1d1ed89ccd0555`). Universe log: v2 manifests and pool snaps from scan `20261101T0430` (2026-11-01T04:30:22Z) through `20261102T0400` (2026-11-02T04:00:28Z), 48 scans.

## Logged post-v3 closes

A close is one lot. Sells of a mint fill the oldest open buy in that book, and the lot closes when the sold fraction reaches 1. Post-v3 means every sell row has a non-blank `cost_model` (here `v3-exit-aware`). 2 lots mix a blank sell with a v3 sell and are left out. The per-trade return is the lot's summed `pnl` divided by the 0.5 SOL ticket, the same scale as the day-14 expectancy. A wiped ticket with the entry haircut still on it lands a little below -100%.

The standard deviation is the sample SD (n−1). Closes needed are the normal approximation for detecting a true mean of that size against 0, at 80% power. Two-sided uses α = 0.05 (z = 1.960 + 0.842). One-sided uses the same power and replaces the first z with 1.645. The formula is n = ceil(((z_α + z_power) × SD / effect)²). It uses each book's own post-v3 SD.

| Book | Closes | Mean | Median | SD | +14% two-sided | +25% two-sided | +14% one-sided | +25% one-sided |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| F | 39 | -11.0% | -43.1% | 111.2% | 495 | 156 | 390 | 123 |
| R | 39 | -4.7% | -42.0% | 114.5% | 525 | 165 | 414 | 130 |
| S | 45 | -46.1% | -74.1% | 71.0% | 202 | 64 | 159 | 50 |

## Price path

The repo has no Jupiter quote path and no OHLC candles for the pre-filter universe. The logged path is the reserve snapshot in `solana-desk/v2/pool-snaps-*.jsonl`. The death log repeats those observation times and adds a label row; the replay reads pool snaps once. The mark is the reserve mid, SOL reserve divided by token reserve. The live book marks exits from the Jupiter quote, which is stored on the fill and not on the other candidates. Liquidity uses the logged SOL reserve, the same input `exits.check` already takes.

At each later snapshot the replay calls the frozen `exits.check` and charges v3 costs with `haircut_frac` / `slice_pnl` (60 bps base, 0.25 × verified constant-product impact, 150 bps on hard_stop, trailing_stop, and liquidity_pulled, cap 600 bps, plus the network fee). A snapshot with no usable mid is a gap. A raw token reserve below 1 is a broken print (the file shows 1e-6) and is not a mid. When that print's SOL reserve is already under the 40% pull line, the remainder closes at 0 (`liquidity_pulled_unpriced`). When the path ends with size still open, the remainder is realized at the last usable mid with a non-stress haircut (`censored_last_mark`).

The pre-filter list is the manifest's candidate mints (chain solana, non-empty address, deduped): about 46 names a scan, of which about 8.7 have a usable entry mid and at least one later point. The draw is uniform over that replayable subset, excluding the book's own mint. Names with no reserve path are not given a synthetic price. A close matches a scan when the buy time is within 180 seconds of the scan. Closes opened before the first manifest have no universe path. They stay in the logged table and stay out of the twin contrast.

## Twin contrast

The contrast replays the book's mint and the drawn mint on the reserve mid, with the same exit rule and the same v3 costs. Each draw stops at the earlier of the two last usable snapshots, so a name the desk kept marking is not given a longer path than the random name. A censored draw is still open at that shared end. The book and the twin can censor at different rates when one name has already hit a stop. Each book gets 2000 draws (seeds: F 20261103, R 20261104, S 20261105). The bootstrap stream for a book is that book's draw seed plus 10000. One draw picks one alternative mint at each twinned entry. The twin mean and median are taken over every simulated per-trade return. The edge is the book's replayed mean minus the mean of those twin returns, which is the average of the per-close gaps versus that close's expected twin. The 95% interval is a percentile bootstrap of that paired mean, 10000 resamples of the closes. The p-value is two-sided: the share of draw means at least as far from the twin center as the book's replayed mean, with 1 added to the numerator and the denominator. It ranks these entries against a redraw at the same times. The overlap is the histogram intersection of the book replay returns and the pooled twin returns, bins of 0.25 from -1.5 to 12.0, with outside mass in the end bins.

F and R share 8 twinned entries (same mint, same open time), so those two rows are not independent samples.

| Book | Twinned | Logged mean | Logged median | Replay mean | Replay median | Twin mean | Twin median | Edge | 95% CI | p | Overlap |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| F | 8 | -40.0% | -52.8% | -25.3% | -42.7% | -13.6% | -6.9% | -11.7% | [-56.9%, +38.6%] | 0.333 | 0.45 |
| R | 8 | -39.4% | -51.2% | -25.3% | -42.7% | -13.3% | -6.9% | -12.0% | [-56.6%, +38.0%] | 0.323 | 0.45 |
| S | 7 | -25.8% | -34.5% | -21.6% | -11.3% | -13.3% | -12.5% | -8.3% | [-49.5%, +35.2%] | 0.641 | 0.65 |

Logged mean and median on this table are the actual ticket returns of the twinned closes. Replay mean and median are the book mint on the matched horizon, averaged across the draws. The twin, the edge, the interval, the p-value, and the overlap use that same horizon.

Book F: 31 post-v3 closes sit before the universe log, 0 match a scan and have no replayable path for the held mint, 0 have a path and no alternative. On the twinned closes the full-path replay, uncapped, sits 20.7% MAE from the logged return (7 of 8 same sign; 1 of those full paths censored). Mean alternatives per entry: 5.88. On the matched horizon, book draws censored: 25.5%, twin draws censored: 58.7%, twin draws closed at 0 on a broken mid: 0.0%.

Book R: 31 post-v3 closes sit before the universe log, 0 match a scan and have no replayable path for the held mint, 0 have a path and no alternative. On the twinned closes the full-path replay, uncapped, sits 19.8% MAE from the logged return (7 of 8 same sign; 1 of those full paths censored). Mean alternatives per entry: 5.88. On the matched horizon, book draws censored: 25.4%, twin draws censored: 57.9%, twin draws closed at 0 on a broken mid: 0.0%.

Book S: 38 post-v3 closes sit before the universe log, 0 match a scan and have no replayable path for the held mint, 0 have a path and no alternative. On the twinned closes the full-path replay, uncapped, sits 3.7% MAE from the logged return (7 of 7 same sign; 1 of those full paths censored). Mean alternatives per entry: 8.0. On the matched horizon, book draws censored: 40.0%, twin draws censored: 27.6%, twin draws closed at 0 on a broken mid: 3.1%.

## Reading

The logged table is the post-v3 record of each book. The twin table is only the closes that have a reserve path for the held mint and for at least one other pre-filter name at the same entry. That window starts at the first v2 manifest. A wide interval and a p-value near one half means these few closes do not separate the book from a random replayable name. The day-14 rule is a different calculation and is not updated here.
