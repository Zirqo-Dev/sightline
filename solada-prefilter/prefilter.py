#!/usr/bin/env python3
"""Solada pre-filter: zero-AI numeric screen for the Solada desk.

Reads launches/market from solada.fun, pool depth from GeckoTerminal (DexScreener
fallback), holders/flows from Blockfrost (Koios fallback), optional Solana holder
concentration via RPC. Applies thresholds parsed from solada/paper.json rules[]
and ticket/log kill lines. POSTs a webhook on new triggers (deduped via state).

Privacy: whale addresses/stake keys are resolved at runtime only; logs and state
hold a short tag. Never prints secrets.
"""
import argparse, json, os, re, sys, time, hashlib, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flags as flagmod  # noqa: E402  flag-only signals, never triggers
import valuation as V  # noqa: E402  LP detection, raw-unit valuation, kill dedupe
from datetime import datetime, timezone

UA = "sightline-solada-prefilter/1.0"
BF = "https://cardano-mainnet.blockfrost.io/api/v0"
KOIOS = "https://api.koios.rest/api/v1"
GT = "https://api.geckoterminal.com/api/v2"
DS = "https://api.dexscreener.com"
SOLADA = "https://www.solada.fun/api"
TOKEN_REGISTRY = "https://tokens.cardano.org/metadata"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Http:
    def __init__(self, pace, backoff=None):
        self.pace, self.last, self.failures, self.calls = pace, {}, {}, {}
        self.backoff = backoff or {}  # src -> [seconds, ...]: extra 429 retries (Retry-After honoured, capped)
        self.sleep = time.sleep

    def _wait(self, src):
        gap = self.pace.get(src, 0.3)
        dt = time.time() - self.last.get(src, 0)
        if dt < gap:
            time.sleep(gap - dt)
        self.last[src] = time.time()

    def req(self, src, url, method="GET", body=None, headers=None, quiet404=True):
        """Return parsed JSON or None. Retries once on 429/5xx/network error."""
        h = {"User-Agent": UA, "Accept": "application/json"}
        h.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        waits = self.backoff.get(src) or [3]
        for attempt in range(len(waits) + 1):
            self._wait(src)
            self.calls[src] = self.calls.get(src, 0) + 1
            try:
                r = urllib.request.Request(url, data=data, method=method, headers=h)
                with urllib.request.urlopen(r, timeout=30) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                if e.code == 404 and quiet404:
                    return None
                if e.code == 429 and attempt < len(waits):
                    ra = (e.headers.get("Retry-After") if getattr(e, "headers", None) else None) or ""
                    w = waits[attempt]
                    if ra.strip().isdigit():
                        w = min(max(w, int(ra.strip())), 60)
                    self.sleep(w)
                    continue
                if e.code >= 500 and attempt == 0:
                    self.sleep(1.5)
                    continue
                self._fail(src, f"http {e.code}")
                return None
            except Exception as e:  # network / json
                if attempt == 0:
                    time.sleep(1.5)
                    continue
                self._fail(src, type(e).__name__)
                return None

    def post_webhook(self, url, body, headers=None):
        """POST the webhook once. True on ANY 2xx (body ignored), False otherwise.

        Never retried after the request may have landed (timeouts/network errors after send, 5xx):
        the trigger stays in state.json pending_triggers and is resent next run with an
        Idempotency-Key. Only a 429 (explicitly not processed) is retried once."""
        h = {"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"}
        h.update(headers or {})
        data = json.dumps(body).encode()
        for attempt in (0, 1):
            self._wait("webhook")
            self.calls["webhook"] = self.calls.get("webhook", 0) + 1
            try:
                r = urllib.request.Request(url, data=data, method="POST", headers=h)
                with urllib.request.urlopen(r, timeout=30) as resp:
                    return 200 <= resp.status < 300
            except urllib.error.HTTPError as e:
                if 200 <= e.code < 300:
                    return True
                if e.code == 429 and attempt == 0:
                    time.sleep(3)
                    continue
                self._fail("webhook", f"http {e.code}")
                return False
            except Exception as e:  # timeout / network: may have landed, do not retry now
                self._fail("webhook", f"{type(e).__name__} (not retried; queued for next run)")
                return False
        return False

    def _fail(self, src, why):
        self.failures[src] = self.failures.get(src, 0) + 1
        print(f"  data failure [{src}]: {why}", file=sys.stderr)


# ---------------------------------------------------------------- thresholds
def num_k(s):
    s = s.replace(",", "").lower()
    m = re.match(r"([\d.]+)\s*([km]?)", s)
    v = float(m.group(1))
    return v * {"k": 1e3, "m": 1e6, "": 1}[m.group(2)]


def parse_rules(paper, defaults=None):
    rules = {r.get("id"): r.get("rule", "") for r in paper.get("rules", [])}
    t = {"source": {}}
    k = rules.get("k", "")
    m = re.search(r"(\d[\d,]*)\+\s*holders", k)
    if m:
        t["min_holders"] = int(m.group(1).replace(",", ""))
    m = re.search(r"ADA[- ]side\s*(?:≥|>=|of at least)\s*~?([\d.,]+\s*[km]?)", k, re.I)
    if m:
        t["min_ada_side_k"] = num_k(m.group(1))
    m = re.search(r"6h buys\s*>\s*sells", k)
    t["k_requires_6h_buys_gt_sells"] = bool(m)
    y = rules.get("y", "")
    m = re.search(r"ADA side under\s*~?([\d.,]+\s*[km]?)", y, re.I)
    if m:
        t["min_ada_side_y"] = num_k(m.group(1))
    t["aa_zero_holder_noise"] = "aa" in rules and "zero-holder" in rules["aa"].lower()
    t["source"] = {"k": k[:120], "y": y[:120], "aa": rules.get("aa", "")[:120]}
    missing = [x for x in ("min_holders", "min_ada_side_k", "min_ada_side_y") if x not in t]
    if missing:
        d = {k: v for k, v in (defaults or {}).items() if not k.startswith("_")}
        still = [x for x in missing if x not in d]
        if still:
            raise SystemExit(f"could not parse thresholds from paper.json rules and no config.json rule_defaults: {still}")
        print(f"::warning::could not parse {missing} from paper.json rules; using config.json rule_defaults", file=sys.stderr)
        for x in missing:
            t[x] = float(d[x]) if x != "min_holders" else int(d[x])
            t["source"].setdefault("defaulted", []).append(x)
    t["min_ada_side"] = max(t["min_ada_side_k"], t["min_ada_side_y"])
    return t


KL_PATTERNS = [
    ("ada_side_below", r"ADA side (?:under|<)\s*~?([\d.,]+\s*[km]?)"),
    ("holders_below", r"holders (?:under|<)\s*~?(\d[\d,]*)"),
    ("mcap_ada_below", r"mcap (?:under|<)\s*~?([\d.,]+\s*[km]?)\s*ADA"),
]


def parse_kill_text(text):
    out = {}
    for key, pat in KL_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            out[key] = num_k(m.group(1))
    return out


def tracked_names(paper, log):
    """ticker -> {kill: {...numbers}, text, asset_id?}. Open tickets win over log text."""
    out = {}
    for tk, text in (log.get("kill_lines") or {}).items():
        if isinstance(text, str):
            kl = parse_kill_text(text)
            if kl:
                # "hit: ..." = Snek already recorded this kill line as hit; the webhook must not report it again
                out[tk.upper()] = {"kill": kl, "text": text, "source": "log.kill_lines",
                                   "desk_hit": text.strip().lower().startswith("hit")}
    for t in paper.get("open", []):
        kl = t.get("kill_line") or {}
        nums = {k: v for k, v in kl.items() if isinstance(v, (int, float)) and k.endswith("_below")}
        if "post_grad_ada_side_below" in nums and "ada_side_below" not in nums:
            nums["ada_side_below"] = nums["post_grad_ada_side_below"]
        if not nums and isinstance(kl.get("text"), str):
            nums = parse_kill_text(kl["text"])
        if nums:
            out[t["ticker"].upper()] = {"kill": nums, "text": kl.get("text", ""), "asset_id": t.get("asset_id"), "source": "paper.open"}
    return out


STAKE_KEY_RE = re.compile(r"\bu[02-9ac-hj-np-z]{5,11}\b")


def watch_keys(log):
    """Stake-key prefixes (after 'stake1') from whale_watch / whale_watch_added in the log + history."""
    keys = set()
    for h in [log] + list(log.get("history") or []):
        ww = h.get("whale_watch")
        if isinstance(ww, dict):
            for w in ww.get("wallets", []):
                keys.update(STAKE_KEY_RE.findall(str(w.get("prefix", ""))))
        for line in h.get("whale_watch_added") or []:
            keys.update(STAKE_KEY_RE.findall(str(line)))
    return sorted(k for k in keys if any(c.isdigit() for c in k))


# ---------------------------------------------------------------- chain data
class Chain:
    def __init__(self, http, bf_key):
        self.h, self.bf_key = http, bf_key
        self.basis = "blockfrost" if bf_key else "koios"
        self.dist = {}  # unit -> [(addr, qty)] from holder pages, reused by flags (no extra calls)

    def bf(self, path):
        if not self.bf_key:
            return None
        return self.h.req("blockfrost", BF + path, headers={"project_id": self.bf_key})

    def koios_get(self, path):
        return self.h.req("koios", KOIOS + path)

    def koios_post(self, path, body):
        return self.h.req("koios", KOIOS + path, method="POST", body=body)

    def holders(self, unit, max_pages):
        """On-chain holder count (addresses with qty>0). Returns (count, capped) or (None, False)."""
        if self.bf_key:
            n, rows = 0, []
            for page in range(1, max_pages + 1):
                r = self.bf(f"/assets/{unit}/addresses?count=100&page={page}")
                if r is None:
                    if page > 1:
                        # a page failed mid-pagination: the count is unknown, never a partial count
                        self.dist.pop(unit, None)
                        self.h._fail("blockfrost", f"holder page {page} failed; holders unknown")
                        return None, False
                    break
                n += sum(1 for a in r if int(a.get("quantity", "0")) > 0)
                rows.extend((a["address"], int(a.get("quantity", "0"))) for a in r)
                if len(r) < 100:
                    self.dist[unit] = rows
                    return n, False
            else:
                self.dist[unit] = rows
                return n, True
            if n:
                return n, False
        pol, name = unit[:56], unit[56:]
        r = self.koios_get(f"/asset_addresses?_asset_policy={pol}&_asset_name={name}&select=payment_address,quantity&limit=1000")
        if r is None:
            return None, False
        self.dist[unit] = [(a.get("payment_address", ""), int(a.get("quantity", "0"))) for a in r]
        return sum(1 for a in r if int(a.get("quantity", "0")) > 0), len(r) >= 1000

    def handle_address(self, policy, name):
        unit = policy + name.encode().hex()
        r = self.bf(f"/assets/{unit}/addresses")
        if r:
            return r[0]["address"]
        # CIP-68 handles carry the 000de140 label
        r = self.bf(f"/assets/{policy}000de140{name.encode().hex()}/addresses")
        if r:
            return r[0]["address"]
        for nm in (name.encode().hex(), "000de140" + name.encode().hex()):
            r = self.koios_get(f"/asset_addresses?_asset_policy={policy}&_asset_name={nm}")
            if r:
                return r[0]["payment_address"]
        return None

    def stake_of(self, addr):
        r = self.bf(f"/addresses/{addr}")
        if r and r.get("stake_address"):
            return r["stake_address"]
        r = self.koios_post("/address_info", {"_addresses": [addr]})
        if r:
            return r[0].get("stake_address")
        return None

    def account_assets(self, stake):
        """{unit: qty, 'lovelace': qty} across the stake key's addresses."""
        out = {}
        if self.bf_key:
            acct = self.bf(f"/accounts/{stake}")
            if acct is not None:
                out["lovelace"] = int(acct.get("controlled_amount", 0)) - int(acct.get("withdrawable_amount", 0) or 0)
                for page in range(1, 40):
                    r = self.bf(f"/accounts/{stake}/addresses/assets?count=100&page={page}")
                    if r is None:
                        return None
                    for a in r:
                        out[a["unit"]] = int(a["quantity"])
                    if len(r) < 100:
                        return out
                return out
        info = self.koios_post("/account_info", {"_stake_addresses": [stake]})
        if not info:
            return None
        out["lovelace"] = int(info[0].get("total_balance", 0)) - int(info[0].get("rewards_available", 0) or 0)
        off = 0
        while True:
            r = self.koios_post(f"/account_assets?offset={off}&limit=1000", {"_stake_addresses": [stake]})
            if r is None:
                return None
            for a in r:
                out[a["policy_id"] + a["asset_name"]] = out.get(a["policy_id"] + a["asset_name"], 0) + int(a["quantity"])
            if len(r) < 1000:
                return out
            off += 1000

    def decimals(self, unit):
        """Token decimals from Koios asset_info (token registry metadata), else the Cardano token registry.
        Returns int or None when unknown. Cached per run."""
        cache = self.__dict__.setdefault("_dec", {})
        if unit in cache:
            return cache[unit]
        d = None
        r = self.koios_post("/asset_info", {"_asset_list": [[unit[:56], unit[56:]]]})
        if r:
            d = ((r[0].get("token_registry_metadata") or {}) or {}).get("decimals")
        if d is None:
            r = self.h.req("tokenregistry", f"{TOKEN_REGISTRY}/{unit}")
            if r:
                d = (r.get("decimals") or {}).get("value")
        cache[unit] = int(d) if d is not None else None
        return cache[unit]

    def account_txs_after(self, stake, after_height, limit):
        """[(tx_hash, block_height)] of the stake key after a block height (newest last), capped."""
        r = self.koios_get(f"/account_txs?_stake_address={stake}&_after_block_height={int(after_height)}&order=block_height.asc&limit={limit}")
        if r is None:
            return None
        return [(t["tx_hash"], t["block_height"]) for t in r]

    def account_tip_height(self, stake):
        r = self.koios_get(f"/account_txs?_stake_address={stake}&order=block_height.desc&limit=1")
        return r[0]["block_height"] if r else None

    def first_block_stakes(self, unit):
        """Stake addresses receiving the asset in the asset's first block (mint block)."""
        txs = None
        if self.bf_key:
            r = self.bf(f"/assets/{unit}/transactions?order=asc&count=25")
            if r is not None:
                txs = [(t["tx_hash"], t["block_height"]) for t in r]
        if txs is None:
            r = self.koios_get(f"/asset_txs?_asset_policy={unit[:56]}&_asset_name={unit[56:]}&_history=true")
            if r is None:
                return None
            txs = sorted(((t["tx_hash"], t["block_height"]) for t in r), key=lambda x: x[1])[:25]
        if not txs:
            return set()
        first = min(b for _, b in txs)
        hashes = [h for h, b in txs if b == first]
        stakes = set()
        for h in hashes:
            outs = None
            if self.bf_key:
                r = self.bf(f"/txs/{h}/utxos")
                if r is not None:
                    outs = [(o["address"], o.get("amount", [])) for o in r.get("outputs", [])]
                    stakes.update(self._bf_stakes(o for o in r.get("inputs", [])))
            if outs is None:
                r = self.koios_post("/tx_utxos", {"_tx_hashes": [h]})
                if not r:
                    continue
                for o in r[0].get("outputs", []) + r[0].get("inputs", []):
                    if o.get("stake_addr"):
                        stakes.add(o["stake_addr"])
                continue
            stakes.update(self._bf_stakes({"address": a} for a, _ in outs))
        return stakes

    def _bf_stakes(self, items):
        s = set()
        for o in items:
            a = o["address"]
            if a.startswith("addr1q") or a.startswith("addr1x") or a.startswith("addr1z") or a.startswith("addr1y"):
                r = self.bf(f"/addresses/{a}")
                if r and r.get("stake_address"):
                    s.add(r["stake_address"])
        return s


GT_MAX_429 = 3


def gt_pool(http, unit):
    """(ada_side, mcap_ada, price_ada, basis) from GeckoTerminal, else DexScreener. Cached per unit per run;
    after GT_MAX_429 rate-limited lookups GeckoTerminal is skipped for the rest of the run."""
    cache = http.__dict__.setdefault("_gt_cache", {})
    if unit in cache:
        return cache[unit]
    cache[unit] = _gt_pool(http, unit)
    return cache[unit]


def _gt_pool(http, unit):
    r = None
    if (http.failures.get("geckoterminal") or 0) < GT_MAX_429:
        r = http.req("geckoterminal", f"{GT}/networks/cardano/tokens/{unit}/pools?page=1")
    elif not http.__dict__.get("_gt_skip_logged"):
        http._gt_skip_logged = True
        print(f"geckoterminal: {GT_MAX_429}+ failed lookups this run; using DexScreener for the rest")
    pools = (r or {}).get("data") or []
    if pools:
        best = max(pools, key=lambda p: float(p["attributes"].get("reserve_in_usd") or 0))
        a = best["attributes"]
        qusd = float(a.get("quote_token_price_usd") or 0)
        res = float(a.get("reserve_in_usd") or 0)
        base_id = (((best.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id", "")
        if base_id.endswith(unit):
            price_ada = float(a.get("base_token_price_quote_token") or 0)
        else:  # pool lists ADA as base: token price = 1 / quote-per-base
            price_ada = float(a.get("quote_token_price_base_token") or 0)
            qusd = float(a.get("base_token_price_usd") or 0)
        mcap_usd = float(a.get("market_cap_usd") or a.get("fdv_usd") or 0)
        if qusd > 0:
            vol = float((a.get("volume_usd") or {}).get("h24") or 0)
            return {"ada_side": round(res / 2 / qusd), "mcap_ada": round(mcap_usd / qusd) if mcap_usd else None,
                    "vol_h24_ada": round(vol / qusd) if vol else None,
                    "price_ada": price_ada, "dex": best["relationships"]["dex"]["data"]["id"] if best.get("relationships") else None,
                    "basis": "geckoterminal"}
    r = http.req("dexscreener", f"{DS}/tokens/v1/cardano/{unit}")
    if r:
        best = max(r, key=lambda p: float((p.get("liquidity") or {}).get("quote") or 0))
        q = float((best.get("liquidity") or {}).get("quote") or 0)
        pn = float(best.get("priceNative") or 0)
        return {"ada_side": round(q), "mcap_ada": None, "price_ada": pn, "dex": best.get("dexId"), "basis": "dexscreener"}
    return None


def solana_concentration(http, rpc, mint, ignore):
    if not rpc or not mint:
        return None
    r = http.req("solana", rpc, method="POST", body={"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [mint]})
    accts = ((r or {}).get("result") or {}).get("value") or []
    if not accts:
        return None
    r2 = http.req("solana", rpc, method="POST", body={"jsonrpc": "2.0", "id": 2, "method": "getMultipleAccounts",
                                                       "params": [[a["address"] for a in accts], {"encoding": "jsonParsed"}]})
    owners = []
    for v in ((r2 or {}).get("result") or {}).get("value") or [None] * len(accts):
        try:
            owners.append(v["data"]["parsed"]["info"]["owner"])
        except Exception:
            owners.append(None)
    sup = http.req("solana", rpc, method="POST", body={"jsonrpc": "2.0", "id": 3, "method": "getTokenSupply", "params": [mint]})
    total = float((((sup or {}).get("result") or {}).get("value") or {}).get("uiAmount") or 0)
    kept = [float(a.get("uiAmount") or 0) for a, o in zip(accts, owners) if o not in ignore]
    if not total or not kept:
        return None
    return {"sol_top1_pct": round(100 * kept[0] / total, 2), "sol_top10_pct": round(100 * sum(kept[:10]) / total, 2)}


def ascii_name(hexname):
    h = hexname[8:] if hexname[:8] in ("0014df10", "000de140", "000643b0") else hexname
    try:
        return bytes.fromhex(h).decode("utf-8", "ignore")
    except ValueError:
        return h


def load_desk_json(rel, desk, repo):
    """(doc, source) from the desk-data checkout first, falling back to main (--repo) only if missing."""
    for base, label in ((desk, "desk-data"), (repo, "main")):
        p = os.path.join(base, rel) if base else None
        if p and os.path.exists(p):
            return json.load(open(p)), label
    raise SystemExit(f"{rel} not found in desk-data or main")


def idempotency_key(cfg, keys, now=None):
    """Hash of the run slot (cron window) plus the trigger keys; stable across retries within a slot."""
    slot_min = int((cfg.get("webhook") or {}).get("idempotency_slot_minutes", 120))
    slot = int((now if now is not None else time.time()) // (slot_min * 60))
    return hashlib.sha256(json.dumps([slot, sorted(keys)]).encode()).hexdigest()[:32]


def tag(s):
    return "w-" + hashlib.sha256(s.encode()).hexdigest()[:8]


# ---------------------------------------------------------------- main
def ping():
    """Webhook test: no scan, no state. Logs only the HTTP status."""
    url = os.environ.get("SOLADA_WEBHOOK_URL", "").strip()
    key = os.environ.get("SOLADA_WEBHOOK_KEY", "").strip()
    missing = [n for n, v in (("SOLADA_WEBHOOK_URL", url), ("SOLADA_WEBHOOK_KEY", key)) if not v]
    if missing:
        print(f"ping: missing secret(s): {', '.join(missing)}")
        return 1
    body = {"reason": "ping", "names": [], "metrics": {}, "note": "Sightline pre-filter webhook test, no action needed"}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "User-Agent": UA, "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception as e:
        print(f"ping: request failed ({type(e).__name__})")
        return 1
    print(f"ping: HTTP {status}")
    return 0 if 200 <= status < 300 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(__file__), ".."))
    ap.add_argument("--data-dir", required=True, help="where state.json / last-run.json go (desk-data checkout)")
    ap.add_argument("--cache-dir", default=os.environ.get("PREFILTER_CACHE_DIR", os.path.expanduser("~/.cache/solada-prefilter")),
                    help="private, uncommitted dir for the whale snapshot (actions/cache in CI)")
    ap.add_argument("--desk-data", default=None,
                    help="desk-data checkout root; solada/paper.json + log.json are read from here first "
                         "(default: parent of --data-dir), falling back to --repo (main)")
    ap.add_argument("--ping", action="store_true", help="skip the scan; POST one test payload to the webhook and exit")
    ap.add_argument("--dry-run", action="store_true", help="never POST the webhook")
    args = ap.parse_args()
    if args.ping:
        return ping()

    cfg = json.load(open(os.path.join(os.path.dirname(__file__), "config.json")))
    desk = args.desk_data or os.path.dirname(os.path.abspath(args.data_dir))
    paper, paper_src = load_desk_json("solada/paper.json", desk, args.repo)
    log, log_src = load_desk_json("solada/log.json", desk, args.repo)
    print(f"desk files: paper.json from {paper_src}, log.json from {log_src}")
    th = parse_rules(paper, cfg.get("rule_defaults"))
    tracked = tracked_names(paper, log)
    wkeys = watch_keys(log)
    os.makedirs(args.data_dir, exist_ok=True)
    state_path = os.path.join(args.data_dir, "state.json")
    state = json.load(open(state_path)) if os.path.exists(state_path) else {}
    baseline = not state.get("seen_launches")
    state.setdefault("seen_launches", [])
    state.setdefault("fired", {})
    state.setdefault("kill_status", {})
    state.setdefault("kill_reported", {})  # ticker -> {metrics: {name: {value, line}}, at, recover_streak}
    state.setdefault("pending_triggers", [])  # triggers whose webhook send failed; retried next run
    for _tk in list(state["kill_reported"]):  # one-time migration from value-hash signatures
        _e = V.migrate_kill_entry(state["kill_reported"][_tk])
        if _e is None:
            state["kill_reported"].pop(_tk)
        else:
            state["kill_reported"][_tk] = _e
    kc = V.kill_cfg(cfg)

    gtc = cfg.get("geckoterminal") or {}
    http = Http(cfg["pace_seconds"], {"geckoterminal": gtc.get("retry_backoff_seconds") or [6, 15]})
    global GT_MAX_429
    GT_MAX_429 = int(gtc.get("max_429_failures", GT_MAX_429))
    bf_key = os.environ.get("BLOCKFROST_PROJECT_ID", "").strip()
    rpc = os.environ.get("SOLANA_RPC_URL", "").strip()
    if not rpc and os.environ.get("HELIUS_API_KEY", "").strip():
        rpc = "https://mainnet.helius-rpc.com/?api-key=" + os.environ["HELIUS_API_KEY"].strip()
    chain = Chain(http, bf_key)
    print(f"solada-prefilter {now_iso()} basis={chain.basis} solana={'on' if rpc else 'off'} dry_run={args.dry_run} baseline={baseline}")
    print(f"thresholds: holders>={th['min_holders']} ada_side>={th['min_ada_side']:.0f} (k {th['min_ada_side_k']:.0f}, y {th['min_ada_side_y']:.0f}); tracked={sorted(tracked)}; watch_keys={len(wkeys)}")

    launches = http.req("solada", SOLADA + "/launches") or []
    market = http.req("solada", SOLADA + "/market") or {}
    quotes = market.get("quotes") or {}
    ada_usd = float(market.get("adaUsdMicro") or 0) / 1e6
    market_age_min = round((time.time() * 1000 - float(market["at"])) / 60000, 1) if market.get("at") else None
    if not launches:
        http._fail("solada", "no launches")

    now_ms = time.time() * 1000
    seen = set(state["seen_launches"])
    new_ids = [l["id"] for l in launches if l["id"] not in seen]
    triggers, results = [], []

    def graduated(l):
        return bool(l.get("pool")) or bool((l.get("curve") or {}).get("graduated"))

    def supply_whole(l):
        raw = float(l.get("totalSupply") or 0)
        return raw / 1e6 if l.get("curve") else raw  # curve launches mint 6-decimal supply

    def unit_of(l):
        return (l.get("policyId") or "") + (l.get("assetName") or "")

    # ---- candidates: new launches (or recent ones on baseline) with a pool or curve
    max_age = cfg["candidate_max_age_hours"] * 3600e3
    cands = [l for l in launches if (l["id"] in new_ids and not baseline) or
             (baseline and now_ms - (l.get("liveAtMs") or 0) < max_age) or
             (graduated(l) and now_ms - (l.get("liveAtMs") or 0) < max_age)]
    holder_budget = cfg["max_holder_lookups"]
    passed = []
    for l in cands:
        u = unit_of(l)
        q = quotes.get(l["id"]) or {}
        row = {"id": l["id"], "ticker": l["ticker"], "unit": u, "graduated": graduated(l), "native_holders": q.get("holders")}
        pool = gt_pool(http, u) if graduated(l) and u else None
        cv = l.get("curve") or {}
        if pool is None and cv and not cv.get("graduated") and cv.get("realLovelace"):
            pool = {"ada_side": round(int(cv["realLovelace"]) / 1e6), "mcap_ada": round(int(cv.get("marketCapLovelace") or 0) / 1e6) or None,
                    "price_ada": float(cv.get("priceAda") or 0), "basis": "solada-curve"}
            row["curve_pct"] = round(int(cv.get("progressBps") or 0) / 100, 1)
        row["ada_side"] = pool["ada_side"] if pool else None
        row["mcap_ada"] = pool["mcap_ada"] if pool else None
        row["pool_basis"] = pool["basis"] if pool else None
        row["vol_h24_ada"] = pool.get("vol_h24_ada") if pool else None
        row["price_ada"] = pool.get("price_ada") if pool else None
        row["decimals"] = 6 if l.get("curve") else None  # Solada curve launches mint 6-decimal supply
        if row["mcap_ada"] is None and pool and pool.get("price_ada"):
            row["mcap_ada"] = round(pool["price_ada"] * supply_whole(l)) or None
        # only spend holder calls when depth already passes (k and y both need depth)
        if row["ada_side"] is not None and row["ada_side"] >= th["min_ada_side"] and holder_budget > 0:
            holder_budget -= 1
            n, capped = chain.holders(u, cfg["max_holder_pages"])
            row["holders"], row["holders_capped"] = n, capped
        if th["aa_zero_holder_noise"] and (row.get("holders") == 0 or (row.get("holders") is None and q.get("holders") == 0)):
            row["verdict"] = "noise (rule aa: zero holders)"
        elif row.get("holders") is not None and row["holders"] >= th["min_holders"] and row["ada_side"] is not None and row["ada_side"] >= th["min_ada_side"]:
            row["verdict"] = "pass k+y"
            passed.append(row)
        else:
            row["verdict"] = "fail"
        results.append(row)
        if row["verdict"] == "pass k+y":
            key = f"launch:{l['id']}"
            if key not in state["fired"]:
                if rpc and l.get("pumpMint"):
                    c = solana_concentration(http, rpc, l["pumpMint"], set(cfg["solana"]["ignore_owners"]))
                    if c:
                        row.update(c)
                triggers.append({"key": key, "kind": "new_launch_pass", "name": l["ticker"],
                                 "metrics": {k: row.get(k) for k in ("ada_side", "mcap_ada", "holders", "pool_basis", "sol_top1_pct", "sol_top10_pct")}})

    # ---- tracked names vs kill lines
    by_ticker = {}
    for l in launches:
        by_ticker.setdefault(l["ticker"].upper(), []).append(l)
    for tk, t in sorted(tracked.items()):
        opts = by_ticker.get(tk, [])
        l = None
        if not t.get("asset_id") and tk in cfg.get("units", {}):
            t["asset_id"] = cfg["units"][tk]
        if t.get("asset_id"):
            l = next((x for x in opts if unit_of(x) == t["asset_id"]), None)
        if l is None and opts:
            l = max(opts, key=lambda x: (graduated(x), (quotes.get(x["id"]) or {}).get("holders") or 0))
        u = t.get("asset_id") or (unit_of(l) if l else None)
        if not u:
            http._fail("match", f"no unit for tracked {tk}")
            continue
        pool = gt_pool(http, u)
        n, _ = chain.holders(u, cfg["max_holder_pages"])
        m = {"ada_side": pool["ada_side"] if pool else None, "mcap_ada": pool["mcap_ada"] if pool else None, "holders": n,
             "vol_h24_ada": pool.get("vol_h24_ada") if pool else None}
        if m["mcap_ada"] is None and pool and pool.get("price_ada") and l is not None and unit_of(l) == u:
            m["mcap_ada"] = round(pool["price_ada"] * supply_whole(l)) or None
        k = t["kill"]
        # label -> (metric field, kill key); an unknown (None) value never crosses a line
        km = {"ada_side": ("ada_side", "ada_side_below"), "holders": ("holders", "holders_below"), "mcap": ("mcap_ada", "mcap_ada_below")}
        lines = {lb: float(k[kk]) for lb, (f, kk) in km.items() if kk in k}
        values = {lb: m[km[lb][0]] for lb in lines}
        crossed_m = {lb: (values[lb], lines[lb]) for lb in lines if values[lb] is not None and values[lb] < lines[lb]}
        crossed = [f"{lb} {values[lb]} < {lines[lb]:.0f}" for lb in ("ada_side", "holders", "mcap") if lb in crossed_m]
        # complete = every metric this kill line checks was actually read (a data gap is not a recovery)
        complete = all(v is not None for v in values.values())
        if crossed or complete:
            state["kill_status"][tk] = bool(crossed)
        results.append({"ticker": tk, "tracked": True, "unit": u, **m, "crossed": crossed, "kill": k})
        fire, entry, reasons = V.kill_decide(state["kill_reported"].get(tk), crossed_m, values, lines, complete, kc)
        if fire and t.get("desk_hit"):
            # already hit on the desk (log.kill_lines "hit: ..."): record as reported, never send
            state["kill_reported"][tk] = {**entry, "at": now_iso(), "desk_hit": True}
            print(f"kill line {tk}: already hit on the desk; recorded, not sent")
        elif fire:
            sig = V.kill_signature(crossed_m)  # ticker + crossed metric names
            vh = hashlib.sha256(json.dumps(sorted(crossed_m.items())).encode()).hexdigest()[:8]
            triggers.append({"key": f"kill:{tk}:{sig}:{vh}", "kind": "kill_line", "name": tk, "kill_sig": sig,
                             "kill_entry": entry, "metrics": {**m, "crossed": crossed, "why": reasons}})
        elif entry is None:
            if state["kill_reported"].pop(tk, None) is not None:
                print(f"kill line {tk}: recovered (hysteresis met)")
        else:
            state["kill_reported"][tk] = entry

    # ---- founder whale
    wcfg = cfg["whale"]
    lp_pols = V.lp_policies(cfg)
    unit_decimals = {unit_of(l): 6 for l in launches if l.get("curve") and unit_of(l)}
    unit_curve = {unit_of(l): round(int((l.get("curve") or {}).get("progressBps") or 0) / 100, 1)
                  for l in launches if unit_of(l) and l.get("curve") and not graduated(l)}
    dust = V.dust_cfg(wcfg)
    whale_summary = None
    addr = chain.handle_address(wcfg["handle_policy"], wcfg["handle"])
    stake = chain.stake_of(addr) if addr else None
    if not stake:
        http._fail("whale", "handle not resolved")
    else:
        wt = tag(stake)
        snap = chain.account_assets(stake)
        os.makedirs(args.cache_dir, exist_ok=True)
        snap_path = os.path.join(args.cache_dir, "whale-snapshot.json")
        cached = json.load(open(snap_path)) if os.path.exists(snap_path) else {}
        prev = cached.get("snapshot") if cached.get("tag") == wt else None
        prev_height = cached.get("height") if cached.get("tag") == wt else None
        whale_summary = {"tag": wt}
        tip_height = None
        if snap is not None and prev is not None:
            # ADA leg of the whale's own txs since the last snapshot (values aggregator buys correctly)
            legs = {}
            if prev_height is not None:
                txs = chain.account_txs_after(stake, prev_height, wcfg.get("max_ada_leg_txs", 50))
                if txs:
                    tip_height = max(h for _, h in txs)
                    if len(txs) >= wcfg.get("max_ada_leg_txs", 50):
                        # window was capped: start the next window at the account tip so it never re-counts
                        # these txs (value_ada's qty check keeps this run's partial legs from overstating)
                        tip_height = chain.account_tip_height(stake) or tip_height
                    ut = flagmod.FlagChain(http, bf_key).tx_utxos([t for t, _ in txs])
                    legs = V.ada_leg_detail(stake, ut, flagmod.addr_info, lp_pols)
                elif txs is not None:
                    tip_height = prev_height
            moves, lp_moves = [], []
            units = set(snap) | set(prev)
            prices_left = wcfg["max_price_lookups"]
            dec_left = wcfg.get("max_decimals_lookups", 15)
            for u in sorted(units):
                if u == "lovelace":
                    continue
                a, b = prev.get(u, 0), snap.get(u, 0)
                if a == b:
                    continue
                dex = V.lp_dex(u, lp_pols)
                if dex:  # LP mint/burn (liquidity add/remove): label it, never a whale buy
                    lp_moves.append({"asset": "LP", "lp": True, "dex": dex, "delta": b - a})
                    continue
                nm = ascii_name(u[56:]).upper()
                mv = {"asset": nm or u[:12], "delta": b - a}
                if a == 0 and b > 0:
                    mv["new_position"] = True
                if u in unit_curve:
                    mv["curve_pct"] = unit_curve[u]
                price, dec = None, unit_decimals.get(u)
                if dec is None and dec_left > 0:  # always: the payload shows whole tokens, never raw units
                    dec_left -= 1
                    dec = chain.decimals(u)
                leg = legs.get(u) or {}
                if not leg and prices_left > 0:
                    prices_left -= 1
                    p = gt_pool(http, u)
                    price = p.get("price_ada") if p else None
                val, basis = V.value_ada(b - a, leg.get("ada"), dec, price, leg.get("qty"))
                if dec is not None:
                    mv["decimals"] = dec
                    mv["qty"] = round((b - a) / 10 ** dec, 4)
                if val is not None:
                    mv["ada_equiv"], mv["value_basis"] = round(val), basis
                if a > 0:
                    if V.is_dust(b - a, dec, val, dust):
                        mv["dust"] = True  # % of position is meaningless for dust (config whale.dust)
                    else:
                        mv["pct"] = round(100 * abs(b - a) / a, 1)
                fire = (mv.get("ada_equiv", 0) >= wcfg["min_ada_equiv"] or mv.get("pct", 0) >= wcfg["min_position_pct"]
                        or mv.get("new_position") or any(x in nm for x in wcfg["always_tickers"]))
                if fire:
                    moves.append(mv)
            if lp_moves:
                whale_summary["lp_moves"] = lp_moves[:20]
            dl = (snap.get("lovelace", 0) - prev.get("lovelace", 0)) / 1e6
            whale_summary["ada_delta"] = round(dl)
            if abs(dl) >= wcfg["min_ada_equiv"]:
                moves.append({"asset": "ADA", "delta": round(dl)})
            whale_summary["moves"] = moves
            if moves:
                triggers.append({"key": f"whale:{wt}:{hashlib.sha256(json.dumps(moves, sort_keys=True).encode()).hexdigest()[:12]}",
                                 "kind": "whale_move", "name": "$cockwhale", "metrics": {"tag": wt, "moves": moves[:20]}})
        if snap is not None:
            # full snapshot stays in the private cache only, never in committed state
            if not args.dry_run:
                if tip_height is None:
                    tip_height = chain.account_tip_height(stake) if prev_height is None else prev_height
                json.dump({"tag": wt, "at": now_iso(), "snapshot": snap, "height": tip_height}, open(snap_path, "w"))
            w = {"tag": wt, "at": now_iso(), "had_prev": prev is not None}
            hist = (state.get("whale") or {}).get("moves", [])
            for mv in (whale_summary.get("moves") or []):
                hist.append({"at": now_iso(), "tag": wt, "asset": mv["asset"], "delta": mv["delta"]})
            w["moves"] = hist[-200:]
            state["whale"] = w

    # ---- watch-list keys in first block of new launches
    fb_hits = []
    if not baseline and wkeys:
        for lid in new_ids[: cfg["max_new_first_block_checks"]]:
            l = next(x for x in launches if x["id"] == lid)
            u = unit_of(l)
            if not u:
                continue
            stakes = chain.first_block_stakes(u)
            if stakes is None:
                continue
            hit = sorted({k for k in wkeys for s in stakes if s.startswith("stake1" + k)})
            if hit:
                fb_hits.append({"ticker": l["ticker"], "keys": hit})
                triggers.append({"key": f"firstblock:{lid}", "kind": "watch_key_first_block", "name": l["ticker"], "metrics": {"keys": hit}})

    # ---- flag-only signals (never added to triggers; see flags.py)
    new_flags, flag_calls = [], {}
    if cfg.get("flags", {}).get("enabled", True):
        try:
            new_flags, flag_calls = flagmod.run_flags(
                http, bf_key, cfg, launches, new_ids, [r for r in results if r.get("unit")], chain.dist,
                flagmod.watch_map(cfg["flags"], wkeys), whale_summary, state, args.cache_dir, baseline, args.dry_run, now_iso,
                unit_value={r["unit"]: {"price_ada": r.get("price_ada"), "decimals": r.get("decimals")} for r in results if r.get("unit")},
                lp_pols=lp_pols)
        except SystemExit:
            raise
        except Exception as e:  # flags must never break the rules screen
            http._fail("flags", type(e).__name__)
    fpath = os.path.join(args.data_dir, "flags.json" if not args.dry_run else "flags-dry-run.json")
    prev_doc = json.load(open(os.path.join(args.data_dir, "flags.json"))) if os.path.exists(os.path.join(args.data_dir, "flags.json")) else {}
    flags_doc = flagmod.assert_public({
        "at": now_iso(), "flag_only": True,
        "note": "Heuristic flags for Snek's review. Not triggers: they do not count toward rules k/y/aa or kill lines and never fire the webhook alone.",
        "baseline": baseline, "new": new_flags, "recent": ((prev_doc.get("recent") or []) + new_flags)[-300:],
        "api_calls": flag_calls,
        "thresholds": {k: v for k, v in cfg["flags"].items() if not k.startswith("_") and k != "snek_prefix_tags"},
    })
    json.dump(flags_doc, open(fpath, "w"), indent=1)
    print(f"flags: {len(new_flags)} new ({', '.join(sorted({f['kind'] for f in new_flags})) or 'none'}); calls {json.dumps(flag_calls)}")

    # ---- dedupe + webhook
    # pending triggers (webhook failed on an earlier run) are retried first; a newer trigger with the same
    # key, or a newer kill trigger for the same ticker, supersedes the queued one
    new_keys = {t["key"] for t in triggers}
    new_kill = {t["name"] for t in triggers if t["kind"] == "kill_line"}
    pending = [t for t in state["pending_triggers"] if t.get("key") and t["key"] not in state["fired"] and t["key"] not in new_keys
               and not (t.get("kind") == "kill_line" and t.get("name") in new_kill)]
    fresh = pending + [t for t in triggers if t["key"] not in state["fired"]]
    NOPUB = ("key", "kill_sig", "kill_entry")
    if baseline:
        print(f"baseline run: {len(fresh)} would-be trigger(s) recorded, not sent")
    payload = {"reason": "threshold", "names": sorted({t["name"] for t in fresh}),
               "metrics": {"at": now_iso(), "triggers": [{k: v for k, v in t.items() if k not in NOPUB} for t in fresh]}}
    if new_flags:  # ride along only; a payload is sent only when a threshold trigger is fresh
        payload["metrics"]["flags"] = new_flags[:40]
    sent = False
    url, key = os.environ.get("SOLADA_WEBHOOK_URL", "").strip(), os.environ.get("SOLADA_WEBHOOK_KEY", "").strip()
    if fresh and not baseline:
        if args.dry_run:
            print("dry-run: webhook not sent")
        elif not url:
            print("SOLADA_WEBHOOK_URL not set: skipping webhook")
        else:
            hdr = {"Authorization": f"Bearer {key}"} if key else {}
            hdr["Idempotency-Key"] = idempotency_key(cfg, [t["key"] for t in fresh])
            sent = http.post_webhook(url, payload, hdr)
            print(f"webhook {'sent' if sent else 'FAILED (queued for retry next run)'}")
    if sent or baseline or (fresh and args.dry_run is False and not url):
        for t in fresh:
            state["fired"][t["key"]] = now_iso()
            if t["kind"] == "kill_line":
                state["kill_reported"][t["name"]] = {**(t.get("kill_entry") or {}), "at": now_iso()}
        state["pending_triggers"] = []
    elif fresh and not args.dry_run:
        state["pending_triggers"] = fresh[-200:]  # retried next run; whale snapshot / seen_launches may advance safely
    state["seen_launches"] = sorted({l["id"] for l in launches} | seen)
    # keep fired map bounded
    if len(state["fired"]) > 2000:
        state["fired"] = dict(sorted(state["fired"].items(), key=lambda x: x[1])[-2000:])

    summary = {
        "at": now_iso(), "basis": chain.basis, "dry_run": args.dry_run, "baseline": baseline,
        "launches": len(launches), "new_launches": 0 if baseline else len(new_ids), "candidates": len(cands),
        "passed_k_y": [r["ticker"] for r in passed], "thresholds": {k: v for k, v in th.items() if k != "source"},
        "market_age_min": market_age_min, "ada_usd": ada_usd, "whale": {k: v for k, v in (whale_summary or {}).items()},
        "first_block_hits": fb_hits, "triggers": [{k: v for k, v in t.items() if k not in NOPUB} for t in fresh],
        "webhook_sent": sent, "flags_new": len(new_flags), "data_failures": http.failures, "api_calls": http.calls, "results": results,
    }
    if not args.dry_run:
        json.dump(state, open(state_path, "w"), indent=1, sort_keys=True)
    json.dump(summary, open(os.path.join(args.data_dir, "last-run.json" if not args.dry_run else "dry-run.json"), "w"), indent=1)
    print(f"whale: {json.dumps(summary['whale'])[:300]}")
    print(json.dumps({k: summary[k] for k in ("launches", "new_launches", "candidates", "passed_k_y", "first_block_hits", "data_failures", "api_calls")}))
    for t in fresh:
        print(f"TRIGGER {t['kind']}: {t['name']} {json.dumps(t['metrics'])[:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
