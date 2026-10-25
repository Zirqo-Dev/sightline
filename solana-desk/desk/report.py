"""Decision rules v6.2 (zero AI)."""
from . import config as C
def mean(x): return sum(x) / len(x) if x else 0.0
def trimmed(x, pct=None):
    pct = C.TRIM_PCT if pct is None else pct
    k = max(1, int(len(x) * pct)) if x else 0
    return sorted(x)[:-k] if k and len(x) > k else list(x)
def decide(day, f_returns, r_returns):
    n = len(f_returns); ef, er = mean(f_returns), mean(r_returns)
    warn = mean(trimmed(f_returns)) <= mean(trimmed(r_returns)) if f_returns and r_returns else False
    if day < 28:
        out = "STOP_CLEAR_FAILURE" if n >= C.MIN_F_CLOSES and ef < C.EARLY_STOP_EXP else "CONTINUE"
    elif n < C.MIN_F_CLOSES: out = "STOP_FILTERS_TOO_STRICT"
    elif ef <= er: out = "KILL"
    elif ef > 0: out = "PASS"
    else: out = "KILL_LOSES_MONEY"
    return dict(decision=out, f_closes=n, f_exp=ef, r_exp=er, trimmed_warning=warn)
