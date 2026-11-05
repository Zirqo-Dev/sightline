"""Random-twin check for books F, R, and S. Analysis only.

Reads desk-data trades and the v2 universe log. Does not enter, exit, sample,
or write a book, and does not change config, day14_verdict.py, or a workflow.

Post-v3 closes are lots in trades.csv whose every sell row has a non-blank
cost_model. A lot is one mint in one book: sells fill the oldest open buy and
the lot closes when the sold fraction reaches 1. The per-trade return is the
summed `pnl` divided by the 0.5 SOL ticket. This check has no cash book.
action=refill rows are cash top-ups and are skipped, so a refill does not
move those returns.

The twin redraws, at each close's entry scan, a different mint from that
scan's pre-filter candidate list and replays the frozen v6.5 exit rule with
v3 costs. Jupiter quote paths are not logged for the universe. The mark is the
reserve mid (SOL reserve / token reserve) on pool-snaps, which is the same
clock as the death-log observations. A raw token reserve below 1 is a broken
print, not a mid.
"""
import argparse, csv, hashlib, json, math, os, random, statistics, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DESK = os.path.join(ROOT, "solana-desk")
if DESK not in sys.path:
    sys.path.insert(0, DESK)

from desk import config as C
from desk import exits
from desk.fills import entry_cost, haircut_frac, slice_pnl, verified_impact_bps
from desk.v2log import reserve_price

BOOKS = ("F", "R", "S")
MATCH_S = 180
MIN_TOKEN = 1.0          # raw token reserve below this is a broken print (logged as 1e-6)
DRAWS = 2000
BOOT = 10000
SEED = 20261102
BIN_LO = -1.5
BIN_HI = 12.0
BIN_WIDTH = 0.25
ALPHA = 0.05
POWER = 0.80
EFFECTS = (0.14, 0.25)
REPORT_MD = os.path.join(ROOT, "reports", "random_twin_2026-11-02.md")
REPORT_JSON = os.path.join(ROOT, "reports", "random_twin_2026-11-02.json")


def _num(v):
    if isinstance(v, bool) or v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ts)))


def build_closes(rows):
    """Lots from append-only trades.csv. Sells fill the oldest open buy of that mint and book."""
    from collections import defaultdict
    lots = defaultdict(list)
    built = []
    for row in rows:
        action = row.get("action")
        if action not in ("buy", "sell"):
            continue  # refill is a cash top-up, not a lot
        book = row.get("book") or ""
        mint = row.get("mint") or ""
        try:
            ts = int(float(row.get("time") or ""))
        except (TypeError, ValueError):
            continue
        key = (book, mint)
        if action == "buy":
            lots[key].append(dict(
                book=book, mint=mint, opened=ts, closed=None,
                pnl=0.0, frac=0.0, n_sell=0, models=[], whys=[],
            ))
            continue
        if not lots[key]:
            continue
        lot = lots[key][0]
        try:
            lot["pnl"] += float(row.get("pnl") or 0)
            lot["frac"] += float(row.get("frac") or 0)
        except (TypeError, ValueError):
            continue
        lot["closed"] = ts
        lot["n_sell"] += 1
        lot["models"].append((row.get("cost_model") or "").strip())
        lot["whys"].append(row.get("why") or "")
        if lot["frac"] >= 1 - 1e-4:
            built.append(lots[key].pop(0))
    for lot in built:
        labels = set(lot["models"])
        lot["post_v3"] = bool(labels) and "" not in labels
        lot["mixed"] = "" in labels and len(labels) > 1
        lot["ret"] = lot["pnl"] / C.TICKET_SOL if C.TICKET_SOL else None
    return built


def load_trades(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def load_v2(data_dir):
    """Scans, candidate mints per scan, and reserve points per mint from pool-snaps."""
    v2 = os.path.join(data_dir, "v2")
    scans = []
    cands = {}
    series = {}
    if not os.path.isdir(v2):
        return scans, cands, series
    names = sorted(os.listdir(v2))
    for name in names:
        if not name.startswith("manifest"):
            continue
        with open(os.path.join(v2, name)) as fh:
            for line in fh:
                row = json.loads(line)
                if row.get("type") == "scan":
                    scans.append(dict(
                        scan_id=row["scan_id"], t_s=float(row["t_s"]),
                        candidates_seen=row.get("candidates_seen"),
                    ))
                    cands.setdefault(row["scan_id"], [])
                elif row.get("type") == "candidate":
                    cands.setdefault(row["scan_id"], []).append(row["mint"])
    points = {}
    for name in names:
        if not name.startswith("pool-snaps"):
            continue
        with open(os.path.join(v2, name)) as fh:
            for line in fh:
                row = json.loads(line)
                t = _num(row.get("t_s"))
                if t is None:
                    continue
                sol = _num(row.get("sol"))
                token = _num(row.get("token"))
                px = reserve_price(sol, token)
                points.setdefault(row["mint"], []).append((t, px, sol, token))
    for mint, rows in points.items():
        rows.sort(key=lambda r: r[0])
        deduped = []
        seen = set()
        for row in rows:
            if row[0] in seen:
                continue
            seen.add(row[0])
            deduped.append(row)
        series[mint] = deduped
    scans.sort(key=lambda s: s["t_s"])
    for sid in cands:
        cands[sid] = sorted(set(cands[sid]))
    return scans, cands, series


def entry_index(path, t, window=MATCH_S):
    """Index of the usable mid closest to t, inside the window. None when there is no mid."""
    best = None
    for i, (ts, px, sol, token) in enumerate(path):
        if px is None or sol is None or token is None:
            continue
        if token < MIN_TOKEN or sol <= 0 or px <= 0:
            continue
        gap = abs(ts - t)
        if gap <= window and (best is None or gap < best[0]):
            best = (gap, i)
    return None if best is None else best[1]


def _book_sell(pos, frac, px, sol, why):
    """Apply one sell the way fills.manage does, and return the net SOL pnl of the slice."""
    dx = C.TICKET_SOL * frac * (px / pos["entry"]) if px > 0 else 0.0
    impact = verified_impact_bps(sol, dx) if dx > 0 else None
    haircut = 0.0 if dx <= 0 else haircut_frac(sol, 0.0 if impact is None else impact, why)
    _gross, net = slice_pnl(pos, frac, px, haircut)
    if not str(why).startswith("take_profit"):
        pos["frac"] = 0.0
    return net


def replay(path, entry_i, until_t=None):
    """Replay v6.5 exits from path[entry_i] on the reserve mid, with v3 costs.

    A later print with a token reserve below MIN_TOKEN is not a mid. When that
    print's SOL reserve is already through the pull line, the remainder closes
    at 0. Any other missing mid is a gap. Points after until_t are ignored.
    When the path ends with size still open, the remainder is realized at the
    last usable mid with a non-stress haircut (censored_last_mark).
    """
    t0, px0, sol0, token0 = path[entry_i]
    if px0 is None or sol0 is None or token0 is None or token0 < MIN_TOKEN or sol0 <= 0 or px0 <= 0:
        return None
    pos = exits.new_position(px0, sol0, t0)
    cost, haircut = entry_cost(sol0)
    pos["entry_cost"] = cost
    pos["entry_haircut"] = haircut
    pnl = 0.0
    why = ""
    future = 0
    last_px, last_sol = px0, sol0
    pull_line = sol0 * (1 - C.EXIT["liq_drop"])
    for t, px, sol, token in path[entry_i + 1:]:
        if until_t is not None and t > until_t:
            break
        broken = token is not None and token < MIN_TOKEN
        if broken:
            if sol is not None and sol < pull_line and pos["frac"] > 1e-9:
                pnl += _book_sell(pos, pos["frac"], 0.0, sol if sol > 0 else None, "liquidity_pulled")
                why = "liquidity_pulled_unpriced"
                return _done(pnl, why, future + 1, False, True)
            continue
        if px is None or sol is None or token is None or px < 0 or sol < 0:
            continue
        future += 1
        last_px, last_sol = px, sol
        acts = exits.check(pos, px, sol, t, "ok")
        for action, frac, reason, apx in acts:
            if action != "sell" or frac <= 0 or pos["frac"] <= 1e-9:
                continue
            pnl += _book_sell(pos, frac, apx, sol, reason)
            why = reason
        if pos["frac"] <= 1e-9:
            return _done(pnl, why, future, False, False)
    if future < 1:
        return None
    pnl += _book_sell(pos, pos["frac"], last_px, last_sol, "censored_last_mark")
    return _done(pnl, "censored_last_mark", future, True, False)


def _done(pnl, why, future, censored, unpriced):
    return dict(
        ret=pnl / C.TICKET_SOL,
        why=why,
        future=future,
        censored=censored,
        unpriced=unpriced,
    )


def last_observed_t(path, entry_i):
    """Last time that can change the replay: a usable mid, or a broken print that can close at 0.

    A later row with no reserve is a gap and does not extend the horizon.
    """
    last = path[entry_i][0]
    for t, px, sol, token in path[entry_i + 1:]:
        broken = token is not None and token < MIN_TOKEN
        usable = (
            px is not None and sol is not None and token is not None
            and token >= MIN_TOKEN and px >= 0 and sol >= 0
        )
        if broken or usable:
            last = t
    return last


def replayable_map(scan, cands, series):
    out = {}
    for mint in cands.get(scan["scan_id"], []):
        path = series.get(mint) or []
        idx = entry_index(path, scan["t_s"])
        if idx is None:
            continue
        got = replay(path, idx)
        if got is None:
            continue
        out[mint] = dict(
            path=path, entry_i=idx, last_t=last_observed_t(path, idx), full=got,
        )
    return out


def match_scan(ts, scans):
    best = None
    for scan in scans:
        gap = abs(scan["t_s"] - ts)
        if best is None or gap < best[0]:
            best = (gap, scan)
    if best is None or best[0] > MATCH_S:
        return None
    return best[1]


def sample_sd(xs):
    if len(xs) < 2:
        return None
    return statistics.stdev(xs)


def closes_needed(sd, delta, alpha=ALPHA, power=POWER, sided="two"):
    """Normal approximation. Two-sided uses z_{1-α/2}; one-sided uses z_{1-α}."""
    if sd is None or sd <= 0 or delta <= 0:
        return None
    normal = statistics.NormalDist()
    z_a = normal.inv_cdf(1 - (alpha / 2 if sided == "two" else alpha))
    z_b = normal.inv_cdf(power)
    n = ((z_a + z_b) * sd / delta) ** 2
    return int(math.ceil(n - 1e-9))


def percentile(sorted_xs, p):
    if not sorted_xs:
        return None
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    k = (len(sorted_xs) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_xs[int(k)]
    return sorted_xs[lo] * (hi - k) + sorted_xs[hi] * (k - lo)


def bootstrap_ci(diffs, n_boot, rng):
    n = len(diffs)
    stats = []
    for _ in range(n_boot):
        total = 0.0
        for _k in range(n):
            total += diffs[rng.randrange(n)]
        stats.append(total / n)
    stats.sort()
    return percentile(stats, 0.025), percentile(stats, 0.975)


def overlap_coefficient(book, twin, lo=BIN_LO, hi=BIN_HI, width=BIN_WIDTH):
    """Histogram intersection. Mass outside [lo, hi) sits in the end bins."""
    if not book or not twin or width <= 0 or hi <= lo:
        return None
    n_bins = int(round((hi - lo) / width))

    def hist(xs):
        counts = [0] * n_bins
        for x in xs:
            if x < lo:
                i = 0
            elif x >= hi:
                i = n_bins - 1
            else:
                i = int((x - lo) / width)
                if i >= n_bins:
                    i = n_bins - 1
            counts[i] += 1
        total = float(sum(counts))
        return [c / total for c in counts]

    ha, hb = hist(book), hist(twin)
    return sum(min(a, b) for a, b in zip(ha, hb))


def _r(x, n=6):
    if x is None:
        return None
    return round(float(x), n)


def _matched(book, alt):
    """Replay both mints through the earlier of their last snapshots."""
    until = min(book["last_t"], alt["last_t"])
    b = replay(book["path"], book["entry_i"], until)
    a = replay(alt["path"], alt["entry_i"], until)
    if b is None or a is None:
        return None
    return b, a


def simulate_book(paired, n_draws, n_boot, seed):
    """paired: list of path records. Each draw caps both mints at the same last snapshot."""
    rng = random.Random(seed)
    boot_rng = random.Random(seed + 10_000)
    n = len(paired)
    picks = []
    for row in paired:
        alts = row["alts"]
        picks.append([alts[rng.randrange(len(alts))] for _ in range(n_draws)])
    book = []
    twin_of = []
    twin_means = []
    pooled = []
    censored_book = 0
    censored_twin = 0
    unpriced = 0
    why = {}
    for d in range(n_draws):
        col_b = []
        col_a = []
        for i in range(n):
            pair = _matched(paired[i], picks[i][d])
            b, a = pair
            col_b.append(b["ret"])
            col_a.append(a["ret"])
            pooled.append(a["ret"])
            if b["censored"]:
                censored_book += 1
            if a["censored"]:
                censored_twin += 1
            if a["unpriced"]:
                unpriced += 1
            why[a["why"]] = why.get(a["why"], 0) + 1
        book.append(col_b)
        twin_of.append(col_a)
        twin_means.append(statistics.mean(col_a))
    # Per close, the book's matched-horizon return averaged over the draws it was paired with,
    # and the expected twin return at that same set of horizons.
    book_ret = [statistics.mean(book[d][i] for d in range(n_draws)) for i in range(n)]
    expected = [statistics.mean(twin_of[d][i] for d in range(n_draws)) for i in range(n)]
    diffs = [book_ret[i] - expected[i] for i in range(n)]
    edge = statistics.mean(diffs)
    lo, hi = bootstrap_ci(diffs, n_boot, boot_rng)
    center = statistics.mean(twin_means)
    book_mean = statistics.mean(book_ret)
    extreme = sum(1 for m in twin_means if abs(m - center) >= abs(book_mean - center) - 1e-12)
    p = (1 + extreme) / (n_draws + 1)
    one_sided = (1 + sum(1 for m in twin_means if m >= book_mean - 1e-12)) / (n_draws + 1)
    flat_book = [book[d][i] for d in range(n_draws) for i in range(n)]
    return dict(
        n=n,
        book_replay_mean=_r(book_mean),
        book_replay_median=_r(statistics.median(flat_book)),
        book_logged_mean=_r(statistics.mean(row["logged"] for row in paired)),
        book_logged_median=_r(statistics.median(row["logged"] for row in paired)),
        twin_mean=_r(statistics.mean(pooled)),
        twin_median=_r(statistics.median(pooled)),
        edge=_r(edge),
        ci95=[_r(lo), _r(hi)],
        p_value=_r(p),
        p_one_sided_twin_ge_book=_r(one_sided),
        overlap=_r(overlap_coefficient(flat_book, pooled), 4),
        draws=n_draws,
        bootstrap=n_boot,
        seed=seed,
        censored_share=_r(censored_twin / len(pooled), 4) if pooled else None,
        book_censored_share=_r(censored_book / len(flat_book), 4) if flat_book else None,
        unpriced_share=_r(unpriced / len(pooled), 4) if pooled else None,
        alt_counts=[len(row["alts"]) for row in paired],
        exit_why=why,
        mae_logged=_r(statistics.mean(abs(row["logged"] - row["full"]["ret"]) for row in paired)),
        sign_agree=sum(
            1 for row in paired
            if (row["logged"] >= 0) == (row["full"]["ret"] >= 0)
        ),
        full_censored=sum(1 for row in paired if row["full"]["censored"]),
    )


def analyze(data_dir, n_draws=DRAWS, n_boot=BOOT, seed=SEED, data_commit=None):
    trades_path = os.path.join(data_dir, "trades.csv")
    rows = load_trades(trades_path)
    closes = build_closes(rows)
    scans, cands, series = load_v2(data_dir)
    by_scan = {scan["scan_id"]: replayable_map(scan, cands, series) for scan in scans}
    coverage = []
    for scan in scans:
        coverage.append(dict(
            scan_id=scan["scan_id"],
            candidates=len(cands.get(scan["scan_id"], [])),
            replayable=len(by_scan[scan["scan_id"]]),
        ))
    post = [c for c in closes if c["book"] in BOOKS and c["post_v3"]]
    mixed = [c for c in closes if c["book"] in BOOKS and c["mixed"]]
    books = {}
    shared = {}
    for name in BOOKS:
        books[name] = _one_book(
            name, post, scans, by_scan, cands, n_draws, n_boot, seed + BOOKS.index(name) + 1,
        )
        for row in books[name]["paired_keys"]:
            shared.setdefault(row, []).append(name)
    f_r = sum(1 for names in shared.values() if "F" in names and "R" in names)
    for name in BOOKS:
        books[name].pop("paired_keys", None)
    return dict(
        analysis="random_twin",
        rules_changed=False,
        data_ref="origin/desk-data:solana-desk",
        data_commit=data_commit,
        trades_sha256=sha256_file(trades_path),
        ticket_sol=C.TICKET_SOL,
        cost_model=C.COST_MODEL,
        match_s=MATCH_S,
        min_token_reserve=MIN_TOKEN,
        bin=dict(lo=BIN_LO, hi=BIN_HI, width=BIN_WIDTH),
        power=dict(alpha=ALPHA, power=POWER),
        scans=dict(
            n=len(scans),
            first=None if not scans else scans[0]["scan_id"],
            last=None if not scans else scans[-1]["scan_id"],
            first_t=None if not scans else iso(scans[0]["t_s"]),
            last_t=None if not scans else iso(scans[-1]["t_s"]),
            mean_candidates=_r(statistics.mean(c["candidates"] for c in coverage), 2) if coverage else None,
            mean_replayable=_r(statistics.mean(c["replayable"] for c in coverage), 2) if coverage else None,
        ),
        sample=dict(
            post_v3=len(post),
            mixed_excluded=len(mixed),
            pre_v3=sum(1 for c in closes if c["book"] in BOOKS and not c["post_v3"] and not c["mixed"] and c["closed"]),
        ),
        f_r_same_entry=f_r,
        books=books,
    )


def _one_book(name, post, scans, by_scan, cands, n_draws, n_boot, seed):
    mine = [c for c in post if c["book"] == name]
    logged = [c["ret"] for c in mine]
    sd = sample_sd(logged)
    paired = []
    keys = []
    before = 0
    no_path = 0
    no_alt = 0
    for close in sorted(mine, key=lambda c: (c["opened"], c["mint"])):
        scan = match_scan(close["opened"], scans)
        if scan is None:
            before += 1
            continue
        universe = by_scan.get(scan["scan_id"]) or {}
        own = universe.get(close["mint"])
        if own is None:
            no_path += 1
            continue
        alts = []
        for mint, got in sorted(universe.items()):
            if mint == close["mint"]:
                continue
            if _matched(own, got) is None:
                continue
            alts.append(got)
        if not alts:
            no_alt += 1
            continue
        paired.append(dict(
            logged=close["ret"], full=own["full"], path=own["path"],
            entry_i=own["entry_i"], last_t=own["last_t"], alts=alts,
        ))
        keys.append((close["opened"], close["mint"]))
    out = dict(
        n=len(mine),
        mean=_r(statistics.mean(logged)) if logged else None,
        median=_r(statistics.median(logged)) if logged else None,
        sd=_r(sd),
        opened_first=iso(min(c["opened"] for c in mine)) if mine else None,
        opened_last=iso(max(c["opened"] for c in mine)) if mine else None,
        closed_last=iso(max(c["closed"] for c in mine)) if mine else None,
        before_universe_log=before,
        in_window_no_path=no_path,
        in_window_no_alternative=no_alt,
        power=dict(
            sd=_r(sd),
            two_sided={str(int(e * 100)): closes_needed(sd, e, sided="two") for e in EFFECTS},
            one_sided={str(int(e * 100)): closes_needed(sd, e, sided="one") for e in EFFECTS},
        ),
        paired_keys=keys,
    )
    if paired:
        out["twin"] = simulate_book(paired, n_draws, n_boot, seed)
        out["twin"]["mean_alternatives"] = _r(statistics.mean(out["twin"].pop("alt_counts")), 2)
    else:
        out["twin"] = None
    return out


def _count(x, digits=0):
    if x is None:
        return "n/a"
    return f"{x:.{digits}f}"


def _pct(x, signed=True):
    if x is None:
        return "n/a"
    v = 100.0 * x
    return f"{v:+.1f}%" if signed else f"{v:.1f}%"


def _p(x):
    if x is None:
        return "n/a"
    return f"{x:.3f}"


def render_markdown(result):
    scans = result["scans"]
    lines = []
    lines.append("# Random twin check, 2026-11-02")
    lines.append("")
    lines.append(
        "Read-only check of books F, R, and S. Nothing here changes an entry, an exit, "
        "a cost, a sampler, the frozen v6.5 config, or a running workflow."
    )
    lines.append("")
    lines.append(
        f"Source: desk-data `solana-desk/trades.csv` at `{result['data_commit'] or 'unspecified'}` "
        f"(sha256 `{result['trades_sha256']}`). "
        f"Universe log: v2 manifests and pool snaps from scan `{scans['first']}` ({scans['first_t']}) "
        f"through `{scans['last']}` ({scans['last_t']}), {scans['n']} scans."
    )
    lines.append("")
    lines.append("## Logged post-v3 closes")
    lines.append("")
    lines.append(
        "A close is one lot. Sells of a mint fill the oldest open buy in that book, and the lot "
        "closes when the sold fraction reaches 1. Post-v3 means every sell row has a non-blank "
        f"`cost_model` (here `{result['cost_model']}`). "
        f"{result['sample']['mixed_excluded']} lots mix a blank sell with a v3 sell and are left out. "
        "The per-trade return is the lot's summed `pnl` divided by the 0.5 SOL ticket, the same scale "
        "as the day-14 expectancy. A wiped ticket with the entry haircut still on it lands a little below -100%."
    )
    lines.append("")
    lines.append(
        "The standard deviation is the sample SD (n−1). Closes needed are the normal approximation "
        f"for detecting a true mean of that size against 0, at {int(POWER * 100)}% power. "
        f"Two-sided uses α = {ALPHA:g} (z = {statistics.NormalDist().inv_cdf(1 - ALPHA / 2):.3f} + "
        f"{statistics.NormalDist().inv_cdf(POWER):.3f}). "
        f"One-sided uses the same power and replaces the first z with "
        f"{statistics.NormalDist().inv_cdf(1 - ALPHA):.3f}. "
        "The formula is n = ceil(((z_α + z_power) × SD / effect)²). It uses each book's own post-v3 SD."
    )
    lines.append("")
    lines.append("| Book | Closes | Mean | Median | SD | +14% two-sided | +25% two-sided | +14% one-sided | +25% one-sided |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name in BOOKS:
        b = result["books"][name]
        pwr = b["power"]
        lines.append(
            f"| {name} | {b['n']} | {_pct(b['mean'])} | {_pct(b['median'])} | {_pct(b['sd'], False)} "
            f"| {pwr['two_sided']['14']} | {pwr['two_sided']['25']} "
            f"| {pwr['one_sided']['14']} | {pwr['one_sided']['25']} |"
        )
    lines.append("")
    lines.append("## Price path")
    lines.append("")
    lines.append(
        "The repo has no Jupiter quote path and no OHLC candles for the pre-filter universe. "
        "The logged path is the reserve snapshot in `solana-desk/v2/pool-snaps-*.jsonl`. "
        "The death log repeats those observation times and adds a label row; the replay reads pool snaps once. "
        "The mark is the reserve mid, SOL reserve divided by token reserve. "
        "The live book marks exits from the Jupiter quote, which is stored on the fill and not on the other candidates. "
        "Liquidity uses the logged SOL reserve, the same input `exits.check` already takes."
    )
    lines.append("")
    lines.append(
        "At each later snapshot the replay calls the frozen `exits.check` and charges v3 costs with "
        "`haircut_frac` / `slice_pnl` (60 bps base, 0.25 × verified constant-product impact, "
        "150 bps on hard_stop, trailing_stop, and liquidity_pulled, cap 600 bps, plus the network fee). "
        "A snapshot with no usable mid is a gap. A raw token reserve below 1 is a broken print "
        "(the file shows 1e-6) and is not a mid. When that print's SOL reserve is already under the "
        "40% pull line, the remainder closes at 0 (`liquidity_pulled_unpriced`). "
        "When the path ends with size still open, the remainder is realized at the last usable mid "
        "with a non-stress haircut (`censored_last_mark`)."
    )
    lines.append("")
    lines.append(
        f"The pre-filter list is the manifest's candidate mints (chain solana, non-empty address, "
        f"deduped): about {_count(scans['mean_candidates'])} names a scan, "
        f"of which about {_count(scans['mean_replayable'], 1)} "
        "have a usable entry mid and at least one later point. The draw is uniform over that replayable "
        "subset, excluding the book's own mint. Names with no reserve path are not given a synthetic price. "
        f"A close matches a scan when the buy time is within {result['match_s']} seconds of the scan. "
        "Closes opened before the first manifest have no universe path. They stay in the logged table and "
        "stay out of the twin contrast."
    )
    lines.append("")
    lines.append("## Twin contrast")
    lines.append("")
    sample_twin = next((result["books"][name].get("twin") for name in BOOKS if result["books"][name].get("twin")), None)
    n_draws = sample_twin["draws"] if sample_twin else DRAWS
    n_boot = sample_twin["bootstrap"] if sample_twin else BOOT
    seed_bits = ", ".join(
        f"{name} {result['books'][name]['twin']['seed']}"
        for name in BOOKS if result["books"][name].get("twin")
    )
    lines.append(
        "The contrast replays the book's mint and the drawn mint on the reserve mid, with the same exit rule and the same v3 costs. "
        "Each draw stops at the earlier of the two last usable snapshots, so a name the desk kept marking is not given a longer path than the random name. "
        "A censored draw is still open at that shared end. The book and the twin can censor at different rates when one name has already hit a stop. "
        f"Each book gets {n_draws} draws (seeds: {seed_bits}). "
        "The bootstrap stream for a book is that book's draw seed plus 10000. "
        "One draw picks one alternative mint at each twinned entry. "
        "The twin mean and median are taken over every simulated per-trade return. "
        "The edge is the book's replayed mean minus the mean of those twin returns, "
        "which is the average of the per-close gaps versus that close's expected twin. "
        f"The 95% interval is a percentile bootstrap of that paired mean, {n_boot} resamples of the closes. "
        "The p-value is two-sided: the share of draw means at least as far from the twin center as the "
        "book's replayed mean, with 1 added to the numerator and the denominator. "
        "It ranks these entries against a redraw at the same times. "
        "The overlap is the histogram intersection of the book replay returns and the pooled twin returns, "
        f"bins of {BIN_WIDTH:.2f} from {BIN_LO} to {BIN_HI}, with outside mass in the end bins."
    )
    lines.append("")
    lines.append(
        f"F and R share {result['f_r_same_entry']} twinned entries (same mint, same open time), "
        "so those two rows are not independent samples."
    )
    lines.append("")
    lines.append(
        "| Book | Twinned | Logged mean | Logged median | Replay mean | Replay median "
        "| Twin mean | Twin median | Edge | 95% CI | p | Overlap |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|")
    for name in BOOKS:
        b = result["books"][name]
        tw = b.get("twin")
        if not tw:
            lines.append(f"| {name} | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |")
            continue
        ci = f"[{_pct(tw['ci95'][0])}, {_pct(tw['ci95'][1])}]"
        lines.append(
            f"| {name} | {tw['n']} | {_pct(tw['book_logged_mean'])} | {_pct(tw['book_logged_median'])} "
            f"| {_pct(tw['book_replay_mean'])} | {_pct(tw['book_replay_median'])} "
            f"| {_pct(tw['twin_mean'])} | {_pct(tw['twin_median'])} "
            f"| {_pct(tw['edge'])} | {ci} | {_p(tw['p_value'])} | {tw['overlap']:.2f} |"
        )
    lines.append("")
    lines.append(
        "Logged mean and median on this table are the actual ticket returns of the twinned closes. "
        "Replay mean and median are the book mint on the matched horizon, averaged across the draws. "
        "The twin, the edge, the interval, the p-value, and the overlap use that same horizon."
    )
    lines.append("")
    for name in BOOKS:
        b = result["books"][name]
        tw = b.get("twin")
        lines.append(
            f"Book {name}: {b['before_universe_log']} post-v3 closes sit before the universe log, "
            f"{b['in_window_no_path']} match a scan and have no replayable path for the held mint, "
            f"{b['in_window_no_alternative']} have a path and no alternative."
            + (
                f" On the twinned closes the full-path replay, uncapped, sits {_pct(tw['mae_logged'], False)} MAE from the logged return "
                f"({tw['sign_agree']} of {tw['n']} same sign; {tw['full_censored']} of those full paths censored). "
                f"Mean alternatives per entry: {tw['mean_alternatives']}. "
                f"On the matched horizon, book draws censored: {_pct(tw['book_censored_share'], False)}, "
                f"twin draws censored: {_pct(tw['censored_share'], False)}, "
                f"twin draws closed at 0 on a broken mid: {_pct(tw['unpriced_share'], False)}."
                if tw else ""
            )
        )
        lines.append("")
    lines.append("## Reading")
    lines.append("")
    lines.append(
        "The logged table is the post-v3 record of each book. The twin table is only the closes that "
        "have a reserve path for the held mint and for at least one other pre-filter name at the same entry. "
        "That window starts at the first v2 manifest. A wide interval and a p-value near one half means "
        "these few closes do not separate the book from a random replayable name. "
        "The day-14 rule is a different calculation and is not updated here."
    )
    lines.append("")
    return "\n".join(lines)


def write_reports(result, md_path, json_path):
    os.makedirs(os.path.dirname(md_path), exist_ok=True)
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    text = render_markdown(result)
    with open(md_path, "w") as fh:
        fh.write(text)
    with open(json_path, "w") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return text


def desk_commit(repo):
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "-C", repo, "rev-parse", "origin/desk-data"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        return None


def main(argv=None):
    p = argparse.ArgumentParser(description="Random twin check for books F, R, and S. Analysis only.")
    p.add_argument("--data-dir", required=True, help="desk-data solana-desk directory (trades.csv and v2/)")
    p.add_argument("--draws", type=int, default=DRAWS)
    p.add_argument("--bootstrap", type=int, default=BOOT)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--data-commit", default=None)
    p.add_argument("--out-md", default=REPORT_MD)
    p.add_argument("--out-json", default=REPORT_JSON)
    args = p.parse_args(argv)
    if args.draws < 1 or args.bootstrap < 1:
        p.error("draws and bootstrap must be positive")
    commit = args.data_commit or desk_commit(ROOT)
    result = analyze(
        args.data_dir, n_draws=args.draws, n_boot=args.bootstrap,
        seed=args.seed, data_commit=commit,
    )
    # The report text quotes the draw count from the result, which follows --draws.
    text = write_reports(result, args.out_md, args.out_json)
    for name in BOOKS:
        b = result["books"][name]
        tw = b.get("twin") or {}
        print(
            f"{name} n={b['n']} mean={b['mean']} twinned={tw.get('n', 0)} "
            f"edge={tw.get('edge')} p={tw.get('p_value')}"
        )
    print(args.out_md)
    print(args.out_json)
    return text


if __name__ == "__main__":
    main()
