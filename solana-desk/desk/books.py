"""F, R, S books. R: random leads from the same runs where F fills, capped at 2x F. S: safety-failed but execution-ok."""
import random
from . import config as C

class Book:
    def __init__(self, name):
        self.name = name; self.cash = C.START_BALANCE_SOL; self.open = {}; self.fills_today = 0; self.closed = []
        self.peak_equity = self.cash; self.would_halt = False

def select_fills(run_leads, books, seed):
    """run_leads: list of (mint, skip_type, reason). Returns {book: [mints]}.
    F: skip_type None. S: skip_type 'safety'. R: random among execution-ok leads (None or safety), only if F filled this run."""
    rng = random.Random(seed)
    F, R, S = books["F"], books["R"], books["S"]
    out = {"F": [], "R": [], "S": []}
    for m, t, _ in run_leads:
        if t is None and len(F.open) + len(out["F"]) < C.MAX_OPEN_F and F.fills_today + len(out["F"]) < C.MAX_NEW_F_PER_DAY:
            out["F"].append(m)
    cap = lambda b, n: max(0, C.R_MATCH_CAP * (F.fills_today + len(out["F"])) - b.fills_today - n)
    if out["F"]:
        pool = [m for m, t, _ in run_leads if t in (None, "safety")]
        rng.shuffle(pool)
        out["R"] = pool[:min(len(out["F"]), cap(R, 0))]
    for m, t, _ in run_leads:
        if t == "safety" and len(out["S"]) < cap(S, 0):
            out["S"].append(m)
    return out
