"""tasks.csv, trades.csv, health.json, lock file."""
import csv, json, os, time, fcntl
TASK_COLS = ["time","run_id","agent","task","mint","book","result","skip_type","reason","source","check_delay_s","cfg_version","seed","error_src"]  # error_src: APIs that failed (dexscreener/rugcheck/jupiter), ";"-joined
# pnl stays the net SOL result (sells and partial exits). gross_pnl / net_pnl are appended so
# older trades.csv files (header is a prefix of this list) keep their pnl cell and gain blank cells.
# haircut_pct is the haircut on this fill, in percent. entry_haircut_pct / exit_haircut_pct split the two sides.
TRADE_COLS = ["time","book","agent","mint","symbol","action","frac","sol","quote_px","impact_pct","fee_sol","haircut_pct","why","pnl","check_delay_s","stale","gross_pnl","net_pnl","entry_haircut_pct","exit_haircut_pct"]

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
