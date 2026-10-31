"""tasks.csv, trades.csv, health.json, lock file."""
import calendar, csv, json, os, time, fcntl
TASK_COLS = ["time","run_id","agent","task","mint","book","result","skip_type","reason","source","check_delay_s","cfg_version","seed","error_src","fail_reason"]  # error_src: APIs that failed (dexscreener/rugcheck/jupiter), ";"-joined. fail_reason: source:code for each failed fetch, ";"-joined. Log only.
FAIL_SOURCES = ("rugcheck", "jupiter", "dexscreener")
# pnl stays the net SOL result (sells and partial exits). gross_pnl / net_pnl are appended so
# older trades.csv files (header is a prefix of this list) keep their pnl cell and gain blank cells.
# haircut_pct is the haircut on this fill, in percent. entry_haircut_pct / exit_haircut_pct split the two sides.
# cost_model / cost_model_started_at / verified_impact_bps are appended. Older files keep their pnl;
# the new cells stay blank and those rows are labelled pre-v3 when the stats are read.
TRADE_COLS = ["time","book","agent","mint","symbol","action","frac","sol","quote_px","impact_pct","fee_sol","haircut_pct","why","pnl","check_delay_s","stale","gross_pnl","net_pnl","entry_haircut_pct","exit_haircut_pct","verified_impact_bps","cost_model","cost_model_started_at"]

def _append(path, cols, row):
    new = not os.path.exists(path)
    if not new:  # schema upgrade: older file without the new trailing columns gets its header rewritten
        with open(path, newline="") as f: rows = list(csv.reader(f))
        if rows and rows[0] != cols and rows[0] == cols[:len(rows[0])]:
            with open(path, "w", newline="") as f:
                w = csv.writer(f); w.writerow(cols)
                for r in rows[1:]: w.writerow(r + [""] * (len(cols) - len(r)) if r != rows[0] else cols)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new: w.writeheader()
        w.writerow({k: row.get(k, "") for k in cols})

def task(d, row): _append(os.path.join(d, "tasks.csv"), TASK_COLS, row)
def trade(d, row): _append(os.path.join(d, "trades.csv"), TRADE_COLS, row)

def fail_tallies(d, day):
    """Fetch-failure counts for one UTC day, from tasks.csv fail_reason. Not a decision input."""
    sources = {k: 0 for k in FAIL_SOURCES}
    reasons = {}
    path = os.path.join(d, "tasks.csv")
    if not os.path.exists(path):
        return sources, reasons
    start = calendar.timegm(time.strptime(day, "%Y-%m-%d"))
    end = start + 86400
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                t = int(float(row.get("time") or ""))
            except (TypeError, ValueError):
                continue
            if t < start or t >= end:
                continue
            cell = (row.get("fail_reason") or "").strip()
            if not cell:
                continue
            for part in cell.split(";"):
                part = part.strip()
                src, sep, code = part.partition(":")
                if not sep or src not in sources or not code:
                    continue
                if any(c not in "abcdefghijklmnopqrstuvwxyz0123456789" for c in code):
                    continue
                sources[src] += 1
                reasons[part] = reasons.get(part, 0) + 1
    return sources, {k: reasons[k] for k in sorted(reasons)}

def health(d, runs, expected_runs, checks, errors, data_failures, day=None):
    h = dict(day=day or time.strftime("%Y-%m-%d"), loop_ran=runs > 0, runs=runs, expected_runs=expected_runs,
             checks=checks, error_rate=round(errors / checks, 4) if checks else None,
             data_failure_rate=round(data_failures / checks, 4) if checks else None,
             void_day=(data_failures / checks > 0.10) if checks else False, ai=False)
    with open(os.path.join(d, "health.json"), "w") as f: json.dump(h, f, indent=1)
    return h

class Lock:
    def __init__(self, path): self.path = path; self.f = None
    def __enter__(self):
        self.f = open(self.path, "w")
        try: fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: self.f.close(); raise RuntimeError("desk already running")
        self.f.write(str(os.getpid())); self.f.flush(); return self
    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN); self.f.close()
