"""R-overlap integrity check. Analysis only: does not change a draw, an entry, or an exit.

The null is a replay of books.select_fills. F's list is fixed by scan order and
the caps. R's list is a uniform prefix of the execution-ok pool (the shuffle),
of that same length, and a mint R already holds does not open. Overlap is an
opened R mint that is also an F ticket opened on that run. Expectation and
variance are exact over those prefixes, summed across runs. Flag when observed
overlap is greater than expected + 2 SE. This flag does not change a day-14 or
day-28 verdict.

The gate bug is a disclosed deviation for day 28: R is drawn when F's selected
list is non-empty, including a mint F already holds, and enter() does not refill
a skipped mint. It does not change the verdict.

Seed and run counts skip pre-test shadow runs on 2026-10-25 through 2026-10-28.
Overlap still counts tickets opened on those days.

The F-only / R-only / shared pnl block is secondary and is not a verdict input.
"""
import argparse, calendar, hashlib, itertools, math, os, sys, time
from collections import OrderedDict, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from desk import books
from desk.fills import EXCLUDED_SAFETY

# Shadow scans before the paper test's first in-test day. Overlap still uses their tickets.
PRETEST_THROUGH = "20261028"

CODE_NOTE = (
    "Disclosed deviation for day 28, and it does not change the verdict. "
    "books.select_fills appends every pass while F is under the open cap and the daily cap, "
    "including a mint F already holds, and that append is what lets R draw. "
    "enter() then skips a mint already held in that book, or a mint the book cannot pay for, "
    "and does not refill the draw. R can open when every selected F mint was skipped, and R "
    "keeps drawing after F's cash runs out because the gate keys on F's list, not on fills. "
    "A future change would ignore held mints before the cap, draw R only for F tickets that "
    "actually open, keep R-held mints out of R's pool, and size that draw to the F tickets "
    "that opened. Books already share the one scan quote for a mint. That change would alter "
    "who gets a ticket, so this script does not do it."
)


def parse_run_id(pid):
    rest = pid.split("-", 1)[1]
    return rest.rsplit("-", 1)[0]


def slot_seed(run_id):
    slot = calendar.timegm(time.strptime(run_id, "%Y%m%dT%H%M"))
    return slot // 60


def in_test(run_id):
    return run_id[:8] > PRETEST_THROUGH


def in_pool(row):
    kind = row.get("skip_type") or ""
    if kind == "":
        return True
    if kind == "safety" and (row.get("reason") or "") not in EXCLUDED_SAFETY:
        return True
    return False


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def load_tasks(data_dir):
    import csv
    with open(os.path.join(data_dir, "tasks.csv"), newline="") as fh:
        return list(csv.DictReader(fh))


def load_positions(data_dir):
    import json
    with open(os.path.join(data_dir, "positions.json")) as fh:
        return json.load(fh)


def group_runs(tasks):
    runs = OrderedDict()
    for row in tasks:
        runs.setdefault(row["run_id"], []).append(row)
    return runs


def entries(positions):
    """Every opened ticket. pnl is set only on a full close."""
    out = []
    books_ = positions.get("books") or {}
    for name in "FRS":
        book = books_.get(name) or {}
        for row in book.get("closed") or []:
            out.append(dict(
                book=name, mint=row["mint"], run=parse_run_id(row["id"]),
                status="closed", pnl=float(row["pnl"]),
                opened=int(row["opened"]), closed=int(row["closed"]),
            ))
        for pid, row in (book.get("open") or {}).items():
            out.append(dict(
                book=name, mint=row["mint"], run=parse_run_id(pid),
                status="open", pnl=None,
                opened=int(row["opened"]), closed=None,
            ))
    return out


def _fmt(x, n=6):
    return f"{x:.{n}f}"


def _shim(st):
    out = {}
    for name in "FRS":
        bk = books.Book(name)
        bk.open = {str(i): 1 for i in range(len(st[name][0]))}
        bk.fills_today = st[name][1]
        out[name] = bk
    return out


def book_state(ents, run_id, now):
    """Open mints and fills-so-far-today, as enter() saw them before this run's tickets."""
    day = run_id[:8]
    st = {}
    for name in "FRS":
        held = []
        fills = 0
        for ent in ents:
            if ent["book"] != name or ent["opened"] >= now:
                continue
            if time.strftime("%Y%m%d", time.gmtime(ent["opened"])) == day:
                fills += 1
            if ent["closed"] is None or ent["closed"] > now:
                held.append(ent["mint"])
        st[name] = (held, fills)
    return st


def leads_of(rows):
    pool = [row for row in rows if in_pool(row)]
    return [(row["mint"], None if (row.get("skip_type") or "") == "" else "safety", row.get("reason") or "") for row in pool]


def null_one(leads, st):
    """Exact mean and variance of opened-R overlap with F tickets that open.

    Returns None when R's draw length is not a fixed prefix of the pool.
    """
    first = books.select_fills(leads, _shim(st), 0)
    if not first["F"]:
        return None
    k = len(first["R"])
    if len(books.select_fills(leads, _shim(st), 1)["R"]) != k:
        return None
    pool = [m for m, t, _ in leads if t in (None, "safety")]
    if len(pool) != len(set(pool)) or k > len(pool):
        return None
    held_f = set(st["F"][0])
    held_r = set(st["R"][0])
    opened_f = {m for m in first["F"] if m not in held_f}
    held_selected = [m for m in first["F"] if m in held_f]
    if k == 0:
        return 0.0, 0.0, opened_f, held_selected
    acc = acc2 = total = 0
    for subset in itertools.combinations(pool, k):
        opened_r = [m for m in subset if m not in held_r]
        ov = sum(1 for m in opened_r if m in opened_f)
        acc += ov
        acc2 += ov * ov
        total += 1
    mu = acc / float(total)
    var = acc2 / float(total) - mu * mu
    return mu, var, opened_f, held_selected


def opened_by_seed(leads, st, seed):
    picks = books.select_fills(leads, _shim(st), seed)
    held_f = set(st["F"][0])
    held_r = set(st["R"][0])
    return [m for m in picks["F"] if m not in held_f], [m for m in picks["R"] if m not in held_r], picks["F"]


def analyze(tasks, positions):
    runs = group_runs(tasks)
    ents = entries(positions)
    by_run = defaultdict(list)
    for ent in ents:
        by_run[ent["run"]].append(ent)

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
    feed_r, feed_f, feed_s = [], [], []
    pool_rank_obs = {}
    replay_runs = replay_matched = 0
    cap_runs = []

    for run_id in sorted(runs):
        rows = runs[run_id]
        pool_rows = [row for row in rows if in_pool(row)]
        feed_index = {}
        for i, row in enumerate(rows, start=1):
            feed_index.setdefault(row["mint"], i)
        pool_index = {row["mint"]: i for i, row in enumerate(pool_rows, start=1)}
        run_ents = by_run.get(run_id, [])
        f_ents = [e for e in run_ents if e["book"] == "F"]
        r_ents = [e for e in run_ents if e["book"] == "R"]
        s_ents = [e for e in run_ents if e["book"] == "S"]
        f_mints = {e["mint"] for e in f_ents}
        overlap = sum(1 for e in r_ents if e["mint"] in f_mints)
        seeds = {(row.get("seed") or "") for row in rows}
        if in_test(run_id):
            seed_values.append(next(iter(seeds)) if len(seeds) == 1 else "mixed")
            try:
                matched = len(seeds) == 1 and int(next(iter(seeds))) == slot_seed(run_id)
            except ValueError:
                matched = False
            if matched:
                seed_match += 1
        mu = None
        if run_ents:
            now = min(e["opened"] for e in run_ents)
            st = book_state(ents, run_id, now)
            leads = leads_of(rows)
            got = null_one(leads, st)
            if got is None and (f_ents or r_ents):
                model_ok = False
            if got is not None:
                mu, var, _opened_f, held_selected = got
                expected += mu
                variance += var
                if held_selected:
                    cap_runs.append(run_id)
            if f_ents or r_ents:
                replay_runs += 1
                try:
                    seed = int(next(iter(seeds)))
                except ValueError:
                    seed = None
                if seed is not None and got is not None:
                    got_f, got_r, _all_f = opened_by_seed(leads, st, seed)
                    if sorted(got_f) == sorted(e["mint"] for e in f_ents) and sorted(got_r) == sorted(e["mint"] for e in r_ents):
                        replay_matched += 1
        if f_ents or r_ents:
            per_run.append(dict(
                run=run_id, n_pool=len(pool_rows), n_f=len(f_ents), k_r=len(r_ents),
                overlap=overlap, expected=mu,
            ))
        if r_ents:
            if any(e["mint"] not in pool_index for e in r_ents) or any(e["mint"] not in pool_index for e in f_ents):
                model_ok = False
            observed += overlap
            r_entries += len(r_ents)
            for ent in r_ents:
                kind = None
                for row in pool_rows:
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
        for ent in f_ents:
            if ent["mint"] in feed_index:
                feed_f.append(feed_index[ent["mint"]])
        for ent in s_ents:
            if ent["mint"] in feed_index:
                feed_s.append(feed_index[ent["mint"]])

    se = math.sqrt(variance) if variance > 0 else 0.0
    if not model_ok:
        flag = "indeterminate"
    elif observed > expected + 2 * se:
        flag = "yes"
    else:
        flag = "no"
    z = None if se == 0 else (observed - expected) / se
    r_runs = {row["run"] for row in per_run if row["k_r"]}
    f_runs = {row["run"] for row in per_run if row["n_f"]}
    passes = set()
    for run_id, rows in runs.items():
        if in_test(run_id) and any((row.get("skip_type") or "") == "" for row in rows):
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
    return dict(
        per_run=per_run, expected=expected, se=se, observed=observed, flag=flag, z=z,
        model_ok=model_ok, r_entries=r_entries, r_pass=r_pass, r_safety=r_safety,
        r_missing=r_missing, seed_unique=len(set(seed_values)),
        seed_runs=len(seed_values), seed_match=seed_match,
        seed_constant=len(seed_values) > 1 and len(set(seed_values)) == 1,
        r_wo_f=sorted(r_runs - f_runs), f_wo_r=sorted(f_runs - r_runs), pass_no_r=pass_no_r,
        feed_r=feed_r, feed_f=feed_f, feed_s=feed_s, pool_rank_obs=pool_rank_obs,
        mean_feed_r=mean(feed_r), mean_feed_f=mean(feed_f), mean_feed_s=mean(feed_s),
        f_only=side(f_keys - r_keys, "F"), r_only=side(r_keys - f_keys, "R"),
        shared_f=side(shared, "F"), shared_r=side(shared, "R"),
        shared_pairs=len(shared),
        f_n=sum(1 for ent in ents if ent["book"] == "F"),
        s_n=sum(1 for ent in ents if ent["book"] == "S"),
        r_runs=len(r_runs), replay_runs=replay_runs, replay_matched=replay_matched,
        cap_runs=cap_runs,
    )


def _hist(counts, keys):
    return " ".join(f"{k}:{counts.get(k, 0)}" for k in keys)


def _mean(x):
    return "na" if x is None else _fmt(x)


def _pnl(x):
    return "na" if x is None else f"{x:.9f}"


def _runs(ids):
    return "none" if not ids else ",".join(ids)


def render(tasks, positions, data_commit, positions_sha256="unchecked", tasks_sha256="unchecked"):
    f = analyze(tasks, positions)
    top = max([12] + f["feed_r"] + f["feed_f"] + f["feed_s"])
    feed_keys = list(range(1, top + 1))
    rank_keys = sorted(f["pool_rank_obs"])
    fo, ro, sf, sr = f["f_only"], f["r_only"], f["shared_f"], f["shared_r"]
    share = "na" if not (f["f_n"] + f["s_n"]) else _fmt(f["f_n"] / float(f["f_n"] + f["s_n"]))
    if f["seed_runs"] > 1 and f["seed_constant"]:
        seed_sentence = f"The same seed is on all {f['seed_runs']} in-test runs. That is a sampler-bias signal."
    elif f["seed_runs"] > 1:
        seed_sentence = (
            f"Seed is the scan slot in minutes (slot//60), not a constant "
            f"({f['seed_unique']} distinct seeds across {f['seed_runs']} in-test runs, "
            f"{f['seed_match']} equal to that formula)."
        )
    else:
        seed_sentence = (
            f"In-test seed rows: {f['seed_runs']} run, {f['seed_match']} matching slot//60."
        )
    lines = [
        f"data_commit: {data_commit}",
        f"positions_sha256: {positions_sha256}",
        f"tasks_sha256: {tasks_sha256}",
        "pool: execution-ok leads in scan order; pass, or safety except not_graduated and missing_data; the list books.select_fills shuffles",
        "null: exact over shuffle prefixes of that pool; a mint R already holds does not open; overlap is with F tickets opened on that run",
        f"seed_counts_after: {PRETEST_THROUGH}",
        f"runs_with_r: {f['r_runs']}",
        f"r_entries: {f['r_entries']}",
        f"observed_overlap: {f['observed']}",
        f"expected_overlap: {_fmt(f['expected'])}",
        f"se: {_fmt(f['se'])}",
        f"z: {'na' if f['z'] is None else _fmt(f['z'])}",
        f"threshold: {_fmt(f['expected'] + 2 * f['se'])}",
        f"flag: {f['flag']}",
        f"model_ok: {'yes' if f['model_ok'] else 'no'}",
        f"replay_matched: {f['replay_matched']}",
        f"replay_runs: {f['replay_runs']}",
        f"seed_unique: {f['seed_unique']}",
        f"seed_runs: {f['seed_runs']}",
        f"seed_matches_slot_min: {f['seed_match']}",
        f"seed_constant: {'yes' if f['seed_constant'] else 'no'}",
        f"r_from_pass: {f['r_pass']}",
        f"r_from_safety: {f['r_safety']}",
        f"r_missing_from_pool: {f['r_missing']}",
        f"r_without_f_runs: {_runs(f['r_wo_f'])}",
        f"f_without_r_runs: {_runs(f['f_wo_r'])}",
        f"held_f_cap_runs: {_runs(f['cap_runs'])}",
        f"runs_with_pass_and_no_r: {f['pass_no_r']}",
        f"feed_rank_r: {_hist({k: f['feed_r'].count(k) for k in feed_keys}, feed_keys)}",
        f"feed_rank_f: {_hist({k: f['feed_f'].count(k) for k in feed_keys}, feed_keys)}",
        f"mean_feed_rank_r: {_mean(f['mean_feed_r'])}",
        f"mean_feed_rank_f: {_mean(f['mean_feed_f'])}",
        f"mean_feed_rank_s: {_mean(f['mean_feed_s'])}",
        "pool_rank_r: " + ("none" if not rank_keys else " ".join(f"{k}:{f['pool_rank_obs'][k]}" for k in rank_keys)),
        f"lifetime_f_entries: {f['f_n']}",
        f"lifetime_s_entries: {f['s_n']}",
        f"lifetime_f_share_of_f_plus_s: {share}",
        "lifetime_note: F/(F+S) over the whole test is the rough 40% benchmark. It is not the shuffle probability and it does not set the flag.",
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
        "day28_note: the gate bug is a disclosed deviation and does not change the day-14 or day-28 verdict",
        "runs:",
    ]
    for row in f["per_run"]:
        exp = "na" if row["expected"] is None else _fmt(row["expected"])
        lines.append(
            f"{row['run']} pool={row['n_pool']} n_f={row['n_f']} k_r={row['k_r']} overlap={row['overlap']} expected={exp}"
        )
    lines.append(
        "conclusion: "
        + seed_sentence + " "
        + "Pre-test shadow runs through 20261028 are out of the seed counts and still in the overlap. "
        f"The null replays books.select_fills: a uniform prefix of the execution-ok pool, then drop mints R already holds. "
        f"{f['r_safety']} of {f['r_entries']} R entries are safety skips and {f['r_pass']} are passes, "
        f"so R is not taken from the post-filter list alone. "
        f"Logged seeds replay {f['replay_matched']} of {f['replay_runs']} F/R runs. "
        f"Observed overlap is {f['observed']} against expected {_fmt(f['expected'])} "
        f"(SE {_fmt(f['se'])}, z {'na' if f['z'] is None else _fmt(f['z'])}); flag is {f['flag']}. "
        f"R runs with no F ticket opened: {_runs(f['r_wo_f'])}. F runs with no R ticket: {_runs(f['f_wo_r'])}. "
        f"Held mints took an F cap slot on {_runs(f['cap_runs'])}. "
        + CODE_NOTE
    )
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description="R overlap check. The null replays books.select_fills.")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--data-commit", default="unknown")
    o = p.parse_args(argv)
    pos = os.path.join(o.data_dir, "positions.json")
    tasks = os.path.join(o.data_dir, "tasks.csv")
    try:
        text = render(
            load_tasks(o.data_dir), load_positions(o.data_dir), o.data_commit,
            sha256_file(pos), sha256_file(tasks),
        )
    except OSError as exc:
        print(f"r_overlap_check: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
