"""R-overlap integrity check. Analysis only: does not change a draw, an entry, or an exit.

Per run, expected overlap = k_R * n_F / n_pool, summed over runs where R opened
a ticket. n_pool is the list books.select_fills shuffles: scan order, skip_type
pass, or safety except the reasons in fills.EXCLUDED_SAFETY (not_graduated,
missing_data). Execution, eligibility, and data skips are not in that list.
k_R and n_F are tickets that actually opened (closed or still open).

Variance is the hypergeometric variance of a draw of k_R names from that pool
with n_F F tickets in it, summed across runs. Flag when observed overlap is
greater than expected + 2 SE. This flag does not change a day-14 or day-28 verdict.

The F-only / R-only / shared pnl block is secondary and is not a verdict input.
"""
import argparse, calendar, csv, json, math, os, sys, time
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from desk.fills import EXCLUDED_SAFETY

CODE_NOTE = (
    "books.select_fills appends every pass while F is under the open cap and the daily cap, "
    "including a mint F already holds. It shuffles that pool with random.Random(seed) and takes a prefix "
    "only when the F list is non-empty. enter() then skips a mint already held in that book, or a mint "
    "the book cannot pay for, and does not refill the draw. R can therefore open when every selected F "
    "mint was skipped. A future fix would draw R only after an F ticket actually opens, and would not "
    "spend a draw slot on a mint that book already holds. That changes who gets a ticket, so this script does not do it."
)


def parse_run_id(pid):
    rest = pid.split("-", 1)[1]
    return rest.rsplit("-", 1)[0]


def slot_seed(run_id):
    slot = calendar.timegm(time.strptime(run_id, "%Y%m%dT%H%M"))
    return slot // 60


def in_pool(row):
    kind = row.get("skip_type") or ""
    if kind == "":
        return True
    if kind == "safety" and (row.get("reason") or "") not in EXCLUDED_SAFETY:
        return True
    return False


def load_tasks(data_dir):
    with open(os.path.join(data_dir, "tasks.csv"), newline="") as fh:
        return list(csv.DictReader(fh))


def load_positions(data_dir):
    with open(os.path.join(data_dir, "positions.json")) as fh:
        return json.load(fh)


def group_runs(tasks):
    runs = OrderedDict()
    for row in tasks:
        runs.setdefault(row["run_id"], []).append(row)
    return runs


def entries(positions):
    """Every opened ticket. status is closed or open. pnl is set only on a full close."""
    out = []
    books = positions.get("books") or {}
    for name in "FRS":
        book = books.get(name) or {}
        for row in book.get("closed") or []:
            out.append(dict(
                book=name, mint=row["mint"], run=parse_run_id(row["id"]),
                status="closed", pnl=float(row["pnl"]),
            ))
        for pid, row in (book.get("open") or {}).items():
            out.append(dict(
                book=name, mint=row["mint"], run=parse_run_id(pid),
                status="open", pnl=None,
            ))
    return out


def hyper_var(n_pool, n_f, k):
    if n_pool <= 1 or k <= 0 or k >= n_pool:
        return 0.0
    p = n_f / float(n_pool)
    return k * p * (1.0 - p) * (n_pool - k) / float(n_pool - 1)


def _fmt(x, n=6):
    return f"{x:.{n}f}"


def analyze(tasks, positions):
    runs = group_runs(tasks)
    ents = entries(positions)
    by_book_run = {name: {} for name in "FRS"}
    for ent in ents:
        by_book_run[ent["book"]].setdefault(ent["run"], []).append(ent)

    seed_values = []
    seed_match = 0
    per_run = []
    expected = 0.0
    variance = 0.0
    observed = 0
    r_entries = 0
    r_pass = 0
    r_safety = 0
    r_missing = 0
    model_ok = True
    exp_pass = 0.0
    feed_r = []
    feed_f = []
    feed_s = []
    pool_rank_obs = {}
    pool_rank_exp = {}

    for run_id in sorted(runs):
        rows = runs[run_id]
        pool = [row for row in rows if in_pool(row)]
        feed_index = {}
        for i, row in enumerate(rows, start=1):
            feed_index.setdefault(row["mint"], i)
        pool_index = {row["mint"]: i for i, row in enumerate(pool, start=1)}
        f_ents = by_book_run["F"].get(run_id, [])
        r_ents = by_book_run["R"].get(run_id, [])
        s_ents = by_book_run["S"].get(run_id, [])
        f_mints = {ent["mint"] for ent in f_ents}
        n_pool = len(pool)
        n_f = len(f_ents)
        k = len(r_ents)
        overlap = sum(1 for ent in r_ents if ent["mint"] in f_mints)
        n_pass = sum(1 for row in pool if (row.get("skip_type") or "") == "")
        seeds = {(row.get("seed") or "") for row in rows}
        seed_values.append(next(iter(seeds)) if len(seeds) == 1 else "mixed")
        try:
            matched = len(seeds) == 1 and int(next(iter(seeds))) == slot_seed(run_id)
        except ValueError:
            matched = False
        if matched:
            seed_match += 1
        if k or n_f:
            e = (k * n_f / float(n_pool)) if n_pool else None
            per_run.append(dict(
                run=run_id, n_pool=n_pool, n_f=n_f, k_r=k, overlap=overlap,
                expected=e, n_pass=n_pass,
            ))
        if k:
            if n_pool == 0 or k > n_pool or any(ent["mint"] not in pool_index for ent in r_ents):
                model_ok = False
            if any(ent["mint"] not in pool_index for ent in f_ents):
                model_ok = False
            if n_pool and n_f <= n_pool and k <= n_pool:
                expected += k * n_f / float(n_pool)
                variance += hyper_var(n_pool, n_f, k)
                exp_pass += k * n_pass / float(n_pool)
            else:
                model_ok = False
            observed += overlap
            r_entries += k
            for ent in r_ents:
                kind = None
                for row in pool:
                    if row["mint"] == ent["mint"]:
                        kind = row.get("skip_type") or ""
                        break
                if kind is None:
                    r_missing += 1
                elif kind == "":
                    r_pass += 1
                else:
                    r_safety += 1
                if ent["mint"] in feed_index:
                    feed_r.append(feed_index[ent["mint"]])
                rank = pool_index.get(ent["mint"])
                if rank is not None:
                    pool_rank_obs[rank] = pool_rank_obs.get(rank, 0) + 1
            if n_pool and k <= n_pool:
                for i in range(1, n_pool + 1):
                    pool_rank_exp[i] = pool_rank_exp.get(i, 0.0) + (k / float(n_pool))
        for ent in f_ents:
            if ent["mint"] in feed_index:
                feed_f.append(feed_index[ent["mint"]])
        for ent in s_ents:
            if ent["mint"] in feed_index:
                feed_s.append(feed_index[ent["mint"]])

    se = math.sqrt(variance)
    flag = "indeterminate" if not model_ok else ("yes" if observed > expected + 2 * se else "no")
    r_runs = {row["run"] for row in per_run if row["k_r"]}
    f_runs = {row["run"] for row in per_run if row["n_f"]}
    r_wo_f = sorted(r_runs - f_runs)
    f_wo_r = sorted(f_runs - r_runs)
    passes = set()
    for run_id, rows in runs.items():
        if any((row.get("skip_type") or "") == "" for row in rows):
            passes.add(run_id)
    pass_no_r = len(passes - r_runs)

    def mean(xs):
        return None if not xs else sum(xs) / float(len(xs))

    def side(keys, book):
        chosen = [ent for ent in ents if ent["book"] == book and (ent["run"], ent["mint"]) in keys]
        closed = [ent["pnl"] for ent in chosen if ent["status"] == "closed"]
        return len(chosen), len(closed), (None if not closed else sum(closed) / float(len(closed)))

    f_keys = {(ent["run"], ent["mint"]) for ent in ents if ent["book"] == "F"}
    r_keys = {(ent["run"], ent["mint"]) for ent in ents if ent["book"] == "R"}
    shared = f_keys & r_keys
    f_only_n, f_only_c, f_only_m = side(f_keys - r_keys, "F")
    r_only_n, r_only_c, r_only_m = side(r_keys - f_keys, "R")
    sh_f_n, sh_f_c, sh_f_m = side(shared, "F")
    sh_r_n, sh_r_c, sh_r_m = side(shared, "R")
    s_n = sum(1 for ent in ents if ent["book"] == "S")
    f_n = sum(1 for ent in ents if ent["book"] == "F")
    chi = 0.0
    for rank, exp_n in pool_rank_exp.items():
        if exp_n > 0:
            chi += (pool_rank_obs.get(rank, 0) - exp_n) ** 2 / exp_n
    return dict(
        per_run=per_run, expected=expected, se=se, observed=observed, flag=flag,
        model_ok=model_ok, r_entries=r_entries, r_pass=r_pass, r_safety=r_safety,
        r_missing=r_missing, exp_pass=exp_pass, seed_unique=len(set(seed_values)),
        seed_runs=len(seed_values), seed_match=seed_match, seed_constant=len(set(seed_values)) == 1,
        r_wo_f=r_wo_f, f_wo_r=f_wo_r, pass_no_r=pass_no_r,
        feed_r=feed_r, feed_f=feed_f, feed_s=feed_s,
        pool_rank_obs=pool_rank_obs, pool_rank_exp=pool_rank_exp, chi=chi,
        mean_feed_r=mean(feed_r), mean_feed_f=mean(feed_f), mean_feed_s=mean(feed_s),
        f_only=(f_only_n, f_only_c, f_only_m), r_only=(r_only_n, r_only_c, r_only_m),
        shared_f=(sh_f_n, sh_f_c, sh_f_m), shared_r=(sh_r_n, sh_r_c, sh_r_m),
        shared_pairs=len(shared), f_n=f_n, s_n=s_n,
        r_runs=len(r_runs),
    )


def _hist(counts, keys):
    return " ".join(f"{k}:{counts.get(k, 0)}" for k in keys)


def _mean(x):
    return "na" if x is None else _fmt(x)


def _pnl(x):
    return "na" if x is None else f"{x:.9f}"


def _runs(ids):
    return "none" if not ids else ",".join(ids)


def render(tasks, positions, data_commit):
    f = analyze(tasks, positions)
    top = max([12] + f["feed_r"] + f["feed_f"] + f["feed_s"])
    feed_keys = list(range(1, top + 1))
    rank_keys = sorted(set(f["pool_rank_obs"]) | set(f["pool_rank_exp"]))
    fo, ro, sf, sr = f["f_only"], f["r_only"], f["shared_f"], f["shared_r"]
    share = "na" if not (f["f_n"] + f["s_n"]) else _fmt(f["f_n"] / float(f["f_n"] + f["s_n"]))
    lines = [
        f"data_commit: {data_commit}",
        "pool: execution-ok leads in scan order; pass, or safety except not_graduated and missing_data; the list books.select_fills shuffles",
        f"runs_with_r: {f['r_runs']}",
        f"r_entries: {f['r_entries']}",
        f"observed_overlap: {f['observed']}",
        f"expected_overlap: {_fmt(f['expected'])}",
        f"se: {_fmt(f['se'])}",
        f"threshold: {_fmt(f['expected'] + 2 * f['se'])}",
        f"flag: {f['flag']}",
        f"model_ok: {'yes' if f['model_ok'] else 'no'}",
        f"seed_unique: {f['seed_unique']}",
        f"seed_runs: {f['seed_runs']}",
        f"seed_matches_slot_min: {f['seed_match']}",
        f"seed_constant: {'yes' if f['seed_constant'] else 'no'}",
        f"r_from_pass: {f['r_pass']}",
        f"r_from_safety: {f['r_safety']}",
        f"r_from_pass_expected: {_fmt(f['exp_pass'])}",
        f"r_missing_from_pool: {f['r_missing']}",
        f"r_without_f_runs: {_runs(f['r_wo_f'])}",
        f"f_without_r_runs: {_runs(f['f_wo_r'])}",
        f"runs_with_pass_and_no_r: {f['pass_no_r']}",
        f"feed_rank_r: {_hist({k: f['feed_r'].count(k) for k in feed_keys}, feed_keys)}",
        f"feed_rank_f: {_hist({k: f['feed_f'].count(k) for k in feed_keys}, feed_keys)}",
        f"mean_feed_rank_r: {_mean(f['mean_feed_r'])}",
        f"mean_feed_rank_f: {_mean(f['mean_feed_f'])}",
        f"mean_feed_rank_s: {_mean(f['mean_feed_s'])}",
        "pool_rank_r: " + " ".join(f"{k}:{f['pool_rank_obs'].get(k, 0)}" for k in rank_keys),
        "pool_rank_expected: " + " ".join(f"{k}:{_fmt(f['pool_rank_exp'].get(k, 0.0))}" for k in rank_keys),
        f"pool_rank_chi2: {_fmt(f['chi'])}",
        "pool_rank_note: descriptive only, not a flag; front-loaded ranks are expected when the draw takes most of a short pool",
        f"lifetime_f_entries: {f['f_n']}",
        f"lifetime_s_entries: {f['s_n']}",
        f"lifetime_f_share_of_f_plus_s: {share}",
        "lifetime_note: F/(F+S) over the whole test is the rough 40% benchmark. It is not the per-run shuffle probability and it does not set the flag.",
        "secondary: not a verdict input",
        f"f_only_entries: {fo[0]}",
        f"f_only_closed: {fo[1]}",
        f"f_only_mean_pnl: {_pnl(fo[2])}",
        f"r_only_entries: {ro[0]}",
        f"r_only_closed: {ro[1]}",
        f"r_only_mean_pnl: {_pnl(ro[2])}",
        f"shared_pairs: {f['shared_pairs']}",
        f"shared_f_closed: {sf[1]}",
        f"shared_f_mean_pnl: {_pnl(sf[2])}",
        f"shared_r_closed: {sr[1]}",
        f"shared_r_mean_pnl: {_pnl(sr[2])}",
        "runs:",
    ]
    for row in f["per_run"]:
        exp = "na" if row["expected"] is None else _fmt(row["expected"])
        lines.append(
            f"{row['run']} pool={row['n_pool']} n_f={row['n_f']} k_r={row['k_r']} overlap={row['overlap']} expected={exp}"
        )
    if f["seed_runs"] > 1 and f["seed_constant"]:
        seed_sentence = f"The same seed is on all {f['seed_runs']} runs. That is a sampler-bias signal."
    elif f["seed_runs"] > 1:
        seed_sentence = (
            f"Seed is the scan slot in minutes (slot//60), not a constant "
            f"({f['seed_unique']} distinct seeds across {f['seed_runs']} runs, {f['seed_match']} equal to that formula)."
        )
    else:
        seed_sentence = f"This file has {f['seed_runs']} run and {f['seed_match']} of its seeds match slot//60."
    lines.append(
        "conclusion: "
        + seed_sentence + " "
        f"The draw is a Fisher-Yates shuffle of the execution-ok pool, then a prefix. "
        f"{f['r_safety']} of {f['r_entries']} R entries are safety skips and {f['r_pass']} are passes "
        f"(about {_fmt(f['exp_pass'])} passes would be expected from the pool mix), "
        f"so R is not taken from the post-filter list alone. "
        f"Observed overlap is {f['observed']} against expected {_fmt(f['expected'])} (SE {_fmt(f['se'])}); flag is {f['flag']}. "
        f"R runs with no F ticket opened: {_runs(f['r_wo_f'])}. F runs with no R ticket: {_runs(f['f_wo_r'])}. "
        + CODE_NOTE
    )
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description="R overlap check from desk-data tasks.csv and positions.json")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--data-commit", default="unknown")
    o = p.parse_args(argv)
    try:
        text = render(load_tasks(o.data_dir), load_positions(o.data_dir), o.data_commit)
    except OSError as exc:
        print(f"r_overlap_check: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
