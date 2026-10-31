"""Seeded Solana desk market. Every response is a pure function of (kind, mint, scan, amount).

The desk's call order is recorded separately. It is not an input.
"""
import csv, json, os
from urllib.parse import parse_qs, urlparse

SOL = "So11111111111111111111111111111111111111112"
BUY_AMT = 500_000_000
TOK = 1_000_000_000
T0 = 1_800_000_000
# Minutes from T0. 18 scans. Two 60-minute gaps and one 3-hour gap.
_MINUTES = [0, 30, 60, 90, 150, 180, 210, 240, 420, 450, 480, 540, 570, 600, 630, 660, 690, 720]
OFFSETS = [m * 60 for m in _MINUTES]
NAMES = [
    "ladder", "hard", "timestop", "pull", "noroute", "twopool",
    "dump", "safety", "pumpfun", "half", "boom",
]
DEX = {
    "ladder": "pumpswap", "hard": "raydium", "timestop": "meteora", "pull": "raydium",
    "noroute": "pumpswap", "twopool": "pumpswap", "dump": "pumpswap", "safety": "raydium",
    "pumpfun": "pumpfun", "half": "meteora", "boom": "pumpfun",
}
MARKET = {"pumpswap": "pump_fun_amm", "raydium": "raydium", "meteora": "meteoraDlmm", "pumpfun": "pump_fun"}
CREATED_MS = (T0 - 7200) * 1000


def mint_of(name):
    i = NAMES.index(name)
    return f"Mint{i:02d}" + "x" * 28 + f"{i:02d}gd"


NAME_OF = {mint_of(n): n for n in NAMES}


def addr(name, which):
    return f"P{which}{name}"


def price_ratio(name, scan):
    if name == "ladder":
        if scan <= 0:
            return 1.0
        if scan == 1:
            return 1.5
        if scan == 2:
            return 2.0
        return 1.4
    if name == "hard":
        return 0.7 if scan >= 2 else 1.0
    return 1.0


def pools_for(name, scan):
    """(which, sol_or_None, token_or_None). A missing side stays out of the pair."""
    if name == "twopool":
        small = 40 if scan < 4 else 12
        return [("a", 200, 2_000_000), ("b", small, 400_000), ("c", None, 5_000)]
    if name == "pull":
        if scan < 1:
            return [("a", 100, 1_000_000)]
        return [("a", 50, 400_000)]
    if name == "dump":
        if scan < 4:
            return [("a", 100, 1_000_000)]
        return [("a", 80, 1_250_000)]
    if name == "half":
        return [("a", 100, None)]
    if name == "pumpfun":
        return [("a", None, None)]
    return [("a", 100, 1_000_000)]


def _pair(name, which, sol, token):
    body = {
        "chainId": "solana",
        "dexId": DEX[name],
        "pairAddress": addr(name, which),
        "quoteToken": {"address": SOL},
        "baseToken": {"address": mint_of(name), "symbol": name[:6]},
        "pairCreatedAt": CREATED_MS,
        "txns": {"h1": {"sells": 5}},
    }
    if sol is None and token is None:
        body["liquidity"] = None
    else:
        liq = {"usd": 10000}
        if sol is not None:
            liq["quote"] = sol
        if token is not None:
            liq["base"] = token
        body["liquidity"] = liq
    return body


def _rug(name, scan):
    markets = []
    for which, _sol, _tok in pools_for(name, scan):
        markets.append({
            "marketType": MARKET[DEX[name]],
            "pubkey": addr(name, which),
            "lp": {"lpLockedPct": 100},
        })
    return {
        "score_normalised": 90 if name in ("safety", "half") else 5,
        "creatorBalance": 0,
        "mintAuthority": None,
        "freezeAuthority": None,
        "topHolders": [],
        "markets": markets,
        "token": {"supply": 1},
    }


def respond(kind, mint, scan, amount):
    """Pure in (kind, mint, scan, amount). Raises RuntimeError for the scripted feed errors."""
    name = NAME_OF.get(mint) if mint else None
    if kind == "profiles":
        return [{"chainId": "solana", "tokenAddress": mint_of(n)} for n in NAMES]
    if kind == "boosts":
        return []
    if name is None:
        raise AssertionError("unknown mint")
    if kind == "dex":
        if name == "boom" and scan == 0:
            raise RuntimeError("dex")
        return {"pairs": [_pair(name, w, sol, tok) for w, sol, tok in pools_for(name, scan)]}
    if kind == "rug":
        if name == "boom" and scan == 1:
            raise RuntimeError("rug")
        return _rug(name, scan)
    if kind == "jup":
        if name == "boom" and scan == 2:
            raise RuntimeError("jup")
        if name == "noroute" and amount != BUY_AMT and scan >= 1:
            return {}
        if amount == BUY_AMT:
            return {"outAmount": str(TOK), "priceImpactPct": "0.01", "routePlan": [1]}
        ratio = price_ratio(name, scan)
        out = int(amount * 0.5 * ratio)
        return {"outAmount": str(max(out, 1)), "priceImpactPct": "0.02", "routePlan": [1]}
    raise AssertionError(kind)


def parse(url):
    if url.endswith("/token-profiles/latest/v1"):
        return "profiles", None, None
    if url.endswith("/token-boosts/latest/v1"):
        return "boosts", None, None
    if "/latest/dex/tokens/" in url:
        return "dex", url.rstrip("/").split("/")[-1], None
    if "rugcheck.xyz/v1/tokens/" in url and url.endswith("/report"):
        return "rug", url.split("/tokens/")[1].split("/report")[0], None
    if "jup.ag" in url and "quote" in url:
        q = parse_qs(urlparse(url).query)
        amount = int(q["amount"][0])
        inp, out = q["inputMint"][0], q["outputMint"][0]
        mint = out if inp == SOL else inp
        return "jup", mint, amount
    raise AssertionError(url)


class Market:
    def __init__(self):
        self.scan = 0
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append(url)
        kind, mint, amount = parse(url)
        return respond(kind, mint, self.scan, amount)


def run_scenario(scan, data_dir):
    """Drive scan(mode='fill') across OFFSETS. Env that health.json copies is pinned for the run."""
    saved = {k: os.environ.get(k) for k in ("GITHUB_ACTIONS", "GITHUB_EVENT_NAME")}
    os.environ.pop("GITHUB_ACTIONS", None)
    os.environ.pop("GITHUB_EVENT_NAME", None)
    try:
        mk = Market()
        os.makedirs(data_dir, exist_ok=True)
        for i, off in enumerate(OFFSETS):
            mk.scan = i
            scan(data_dir, get=mk.get, now=T0 + off, mode="fill", sleep=lambda s: None)
        return list(mk.calls)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _strip(node, keys):
    if isinstance(node, dict):
        return {k: _strip(v, keys) for k, v in node.items() if k not in keys}
    if isinstance(node, list):
        return [_strip(v, keys) for v in node]
    return node


# Logging only. The golden file is the decision record; these keys are not inputs.
_TASK_LOG = {"check_delay_s", "fail_reason"}
_HEALTH_LOG = ("data_fail_by_source", "data_fail_by_reason")


def _rows(data_dir, name):
    path = os.path.join(data_dir, name)
    drop = _TASK_LOG if name == "tasks.csv" else {"check_delay_s"}
    with open(path, newline="") as fh:
        return [{k: v for k, v in row.items() if k not in drop} for row in csv.DictReader(fh)]


def snapshot(data_dir, calls, strip_keys):
    """Decision view. Log-only position keys, check_delay_s, and fetch-failure logs are removed."""
    keys = set(strip_keys)
    def load(name):
        with open(os.path.join(data_dir, name)) as fh:
            return json.load(fh)
    health = load("health.json")
    for k in _HEALTH_LOG:
        health.pop(k, None)
    return {
        "positions": _strip(load("positions.json"), keys),
        "paper": _strip(load("paper.json"), keys),
        "trades": _rows(data_dir, "trades.csv"),
        "tasks": _rows(data_dir, "tasks.csv"),
        "state": load("state.json"),
        "health": health,
        "calls": list(calls),
    }


def scenario_facts():
    """The fixture covers the shapes the review asked for. This does not touch the desk."""
    if len(NAMES) < 8 or len(OFFSETS) < 16:
        raise AssertionError("scenario size")
    deltas = [b - a for a, b in zip(OFFSETS, OFFSETS[1:])]
    if 3600 not in deltas or 10800 not in deltas:
        raise AssertionError(deltas)
    if not {"pumpswap", "raydium", "meteora", "pumpfun"} <= set(DEX.values()):
        raise AssertionError(set(DEX.values()))
    two = respond("dex", mint_of("twopool"), 0, None)["pairs"]
    sol_pairs = [p for p in two if (p.get("quoteToken") or {}).get("address") == SOL]
    if len(sol_pairs) < 2:
        raise AssertionError("second pool")
    if not any("quote" not in (p.get("liquidity") or {}) for p in two):
        raise AssertionError("missing quote side")
    half = respond("dex", mint_of("half"), 0, None)["pairs"][0]["liquidity"]
    if "base" in half or "quote" not in half:
        raise AssertionError("missing token side")
    for kind, scan in (("dex", 0), ("rug", 1), ("jup", 2)):
        try:
            respond(kind, mint_of("boom"), scan, BUY_AMT if kind == "jup" else None)
        except RuntimeError:
            pass
        else:
            raise AssertionError(kind)
    for scan in (1, 2, 3):
        if respond("jup", mint_of("noroute"), scan, TOK) != {}:
            raise AssertionError("no-route streak")
    if "routePlan" not in respond("jup", mint_of("noroute"), 1, BUY_AMT):
        raise AssertionError("buy quote")

    def sides(name, scan):
        liq = respond("dex", mint_of(name), scan, None)["pairs"][0]["liquidity"]
        return liq["quote"] * liq["base"], liq["quote"], liq["base"]

    k0, s0, t0 = sides("dump", 3)
    k1, s1, t1 = sides("dump", 4)
    if not (k0 == k1 and s1 < s0 and t1 > t0):
        raise AssertionError("dump shape")
    w0, a0, b0 = sides("pull", 0)
    w1, a1, b1 = sides("pull", 1)
    if not (w1 < w0 and a1 < a0 and b1 < b0):
        raise AssertionError("withdrawal shape")
    # Same inputs, same payload, including a second call.
    if respond("dex", mint_of("dump"), 5, None) != respond("dex", mint_of("dump"), 5, None):
        raise AssertionError("not pure")
