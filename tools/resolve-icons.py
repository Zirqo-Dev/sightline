#!/usr/bin/env python3
"""Build-time icon resolver for the Sightline v2 mock.

Reads the repo's desk data (read-only), resolves an icon for every coin ticker and
NFT collection the dashboard shows, downloads it, normalizes it to a 64x64 PNG and
writes <site>/dashboard/icons/icons.json. The browser only ever loads local files.
When nothing resolves, a monogram SVG (colored circle + initials) is generated.

Usage: resolve-icons.py [--refresh] <repo_or_site_dir>

A committed non-monogram icon is kept as-is. Pass --refresh to ask the network
again; if that fetch fails, the previous file stays. icons.json is replaced
only after every icon has been resolved. The site build does not run this.

Env keys used if present (values never printed): HELIUS_API_KEY, BLOCKFROST_PROJECT_ID,
OPENSEA_API_KEY, COINGECKO_API_KEY.
"""
import hashlib, io, json, os, pathlib, re, sys, time, urllib.parse, urllib.request
from PIL import Image

args = [a for a in sys.argv[1:] if a != "--refresh"]
REFRESH = "--refresh" in sys.argv[1:]
if len(args) != 1:
    sys.exit("usage: resolve-icons.py [--refresh] <repo_or_site_dir>")
SITE = pathlib.Path(args[0]).resolve()
OUT = SITE / "dashboard" / "icons"; OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (sightline-icon-resolver)", "Accept": "application/json"}
CACHE = pathlib.Path(__file__).with_name(".icon-http-cache"); CACHE.mkdir(exist_ok=True)
stats = {}  # source -> {"tried": n, "hit": n, "errors": {...}}

def stat(src, ok=None, err=None):
    s = stats.setdefault(src, {"tried": 0, "hit": 0, "errors": {}})
    if ok is not None:
        s["tried"] += 1; s["hit"] += int(bool(ok))
    if err: s["errors"][err] = s["errors"].get(err, 0) + 1

def http(url, data=None, headers=None, raw=False, src="?", pause=0.0):
    key = hashlib.sha1((url + json.dumps(data or "")).encode()).hexdigest()
    cp = CACHE / key
    if cp.exists() and not raw:
        b = cp.read_bytes()
        return json.loads(b) if b else None
    h = dict(UA); h.update(headers or {})
    body = json.dumps(data).encode() if data is not None else None
    if body: h["Content-Type"] = "application/json"
    for attempt in range(3):
        try:
            time.sleep(pause)
            with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=h), timeout=20) as r:
                b = r.read()
                if raw: return b
                cp.write_bytes(b)
                return json.loads(b) if b else None
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2: time.sleep(8 * (attempt + 1)); continue
            stat(src, err=f"HTTP {e.code}"); return None
        except Exception as e:
            stat(src, err=type(e).__name__); return None

def save_image(url, name):
    if not url: return None
    if url.startswith("ipfs://"): url = "https://ipfs.io/ipfs/" + re.sub(r"^ipfs/", "", url[7:])
    urls = [url]
    m = re.search(r"/ipfs/([A-Za-z0-9]+.*)$", url)
    if m: urls += ["https://ipfs.io/ipfs/" + m.group(1), "https://cloudflare-ipfs.com/ipfs/" + m.group(1)]
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA["User-Agent"]})
            with urllib.request.urlopen(req, timeout=25) as r: b = r.read()
            if b.lstrip()[:5] in (b"<svg ", b"<?xml"):
                p = OUT / f"{name}.svg"; p.write_bytes(b); return p.name
            im = Image.open(io.BytesIO(b)); im.seek(0); im = im.convert("RGBA")
            w, h = im.size; s = min(w, h)
            im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s)).resize((64, 64), Image.LANCZOS)
            p = OUT / f"{name}.png"; im.save(p, optimize=True); return p.name
        except Exception:
            continue
    return None

def monogram(label, name, hue_seed):
    words = re.findall(r"[A-Za-z0-9]+", label) or ["?"]
    ini = (words[0][:2] if len(words) == 1 else words[0][0] + words[1][0]).upper()
    hue = int(hashlib.md5(hue_seed.encode()).hexdigest()[:4], 16) % 360
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
           f'<stop offset="0" stop-color="hsl({hue},70%,46%)"/><stop offset="1" stop-color="hsl({(hue+40)%360},70%,28%)"/></linearGradient></defs>'
           f'<circle cx="32" cy="32" r="31" fill="url(#g)" stroke="hsl({hue},80%,65%)" stroke-width="2"/>'
           f'<text x="32" y="41" text-anchor="middle" font-family="Menlo,Consolas,monospace" font-size="{24 if len(ini)>1 else 28}" font-weight="700" fill="#fff">{ini}</text></svg>')
    p = OUT / f"{name}.svg"; p.write_text(svg); return p.name

def safe(s): return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

def load(rel):
    p = SITE / rel
    return json.loads(p.read_text()) if p.exists() else None

# ---------------- collect what the dashboard shows ----------------
coins = {}  # TICKER -> {chain, asset_id, mint}
def add_coin(t, **kw):
    if not t or not re.match(r"^[A-Za-z0-9$]{2,15}$", t): return
    c = coins.setdefault(t.upper(), {"label": t})
    for k, v in kw.items():
        if v and not c.get(k): c[k] = v
for book in ("solada/paper.json", "solada/pumpfun/paper.json"):
    j = load(book) or {}
    for t in (j.get("open") or []) + (j.get("closed") or []):
        add_coin(t.get("ticker"), chain=t.get("side") or j.get("chain"), asset_id=t.get("asset_id"), mint=t.get("mint"))
log = load("solada/log.json") or {}
for src in ("kill_lines", "levels"):
    for k, v in (log.get(src) or {}).items():
        if k.startswith("new_"): continue
        add_coin(k, chain="solana-side" if re.search(r"Solana side|\(Solana\)", str(v)) else "cardano")
nfts = {}
nlog = load("nft/log.json") or {}
for run in (nlog.get("runs") or [])[:1]:
    for w in run.get("watchlist") or []:
        nfts[w["slug"]] = {"label": w["name"], "chain": w.get("chain")}
for t in (load("nft/paper.json") or {}).get("open", []) + (load("nft/paper.json") or {}).get("closed", []):
    nfts.setdefault(t["ticker"], {"label": t.get("name") or t["ticker"], "chain": t.get("chain")})
# legacy NFT site watchlist: extra slug aliases (OpenSea slugs) by name
legacy = {}
for c in (load("nft/site/watchlist.json") or {}).get("collections", []):
    legacy[c["name"].lower()] = c

# ---------------- coin sources ----------------
# Fetched only when an icon is actually missing, so a full local set never calls out.
_solada = None
def solada_data():
    global _solada
    if _solada is None:
        launches = http("https://www.solada.fun/api/launches", src="solada.fun") or []
        market = (http("https://www.solada.fun/api/market", src="solada.fun") or {}).get("quotes", {})
        _solada = (launches, market)
    return _solada
def solada_pick(t, c):
    solada_launch, solada_mkt = solada_data()
    xs = [x for x in solada_launch if (x.get("ticker") or "").upper() == t]
    if c.get("asset_id"):
        exact = [x for x in xs if (x.get("policyId") or "") + (x.get("assetName") or "") == c["asset_id"]]
        if exact: return exact[0]
    if not xs: return None
    return max(xs, key=lambda x: (solada_mkt.get(x["id"], {}).get("holders") or 0, solada_mkt.get(x["id"], {}).get("marketCapUsd") or 0))

H = os.environ.get("HELIUS_API_KEY"); BF = os.environ.get("BLOCKFROST_PROJECT_ID")
OS_KEY = os.environ.get("OPENSEA_API_KEY"); CG_KEY = os.environ.get("COINGECKO_API_KEY")

def coin_sources(t, c):
    x = solada_pick(t, c)
    if x:
        c.setdefault("asset_id", (x.get("policyId") or "") + (x.get("assetName") or "") or None)
        if x.get("pumpMint"): c.setdefault("solana_mint", x["pumpMint"])
    yield "solada.fun", (x or {}).get("imageUri")
    unit = c.get("asset_id")
    if unit:
        j = http(f"https://api.geckoterminal.com/api/v2/networks/cardano/tokens/{unit}", src="geckoterminal", pause=2.1)
        yield "geckoterminal", (((j or {}).get("data") or {}).get("attributes") or {}).get("image_url")
    mint = c.get("mint") or c.get("solana_mint")
    if mint:
        j = http(f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}", src="geckoterminal", pause=2.1)
        yield "geckoterminal", (((j or {}).get("data") or {}).get("attributes") or {}).get("image_url")
    for chain, addr in (("cardano", unit), ("solana", mint)):
        if not addr: continue
        j = http(f"https://api.dexscreener.com/tokens/v1/{chain}/{addr}", src="dexscreener")
        img = next((p.get("info", {}).get("imageUrl") for p in (j or []) if p.get("info", {}).get("imageUrl")), None)
        yield "dexscreener", img
    if unit:
        j = http(f"https://tokens.cardano.org/metadata/{unit}", src="cardano-token-registry")
        logo = ((j or {}).get("logo") or {}).get("value")
        yield "cardano-token-registry", ("data:image/png;base64," + logo) if logo else None
        if BF:
            j = http(f"https://cardano-mainnet.blockfrost.io/api/v0/assets/{unit}", headers={"project_id": BF}, src="blockfrost")
            md = (j or {}).get("onchain_metadata") or {}
            img = md.get("image") or ((j or {}).get("metadata") or {}).get("logo")
            if isinstance(img, list): img = "".join(img)
            if img and not str(img).startswith(("http", "ipfs")): img = "data:image/png;base64," + img
            yield "blockfrost", img
    if mint and H:
        j = http(f"https://mainnet.helius-rpc.com/?api-key={H}", data={"jsonrpc": "2.0", "id": 1, "method": "getAsset", "params": {"id": mint}}, src="helius-das")
        yield "helius-das", ((((j or {}).get("result") or {}).get("content") or {}).get("links") or {}).get("image")
    # CoinGecko by symbol, only for the desk's platform token (tickers like BTC here are memecoins, not bitcoin)
    if t == "SOLADA":
        j = http("https://api.coingecko.com/api/v3/search?query=solada", headers={"x-cg-demo-api-key": CG_KEY} if CG_KEY else None, src="coingecko")
        hit = next((x for x in (j or {}).get("coins", []) if x.get("symbol", "").upper() == "SOLADA"), None)
        yield "coingecko", (hit or {}).get("large")
        j = http("https://api.geckoterminal.com/api/v2/search/pools?query=SOLADA&network=cardano&include=base_token", src="geckoterminal", pause=2.1)
        img = next((i["attributes"].get("image_url") for i in (j or {}).get("included", []) if i["attributes"].get("symbol", "").upper() == "SOLADA" and i["attributes"].get("image_url")), None)
        yield "geckoterminal", img
        j = http("https://api.dexscreener.com/latest/dex/search?q=SOLADA", src="dexscreener")
        img = next((p.get("info", {}).get("imageUrl") for p in (j or {}).get("pairs", []) or [] if p.get("baseToken", {}).get("symbol", "").upper() == "SOLADA" and p.get("chainId") == "cardano" and p.get("info", {}).get("imageUrl")), None)
        yield "dexscreener", img
        # Last resort for the launchpad's own token: the launchpad's site icon
        yield "solada.fun (site icon)", "https://www.solada.fun/favicon.ico"

def save_data_uri(uri, name):
    import base64
    try:
        im = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))).convert("RGBA").resize((64, 64), Image.LANCZOS)
        p = OUT / f"{name}.png"; im.save(p); return p.name
    except Exception:
        return None

def nft_sources(slug, n):
    lg = legacy.get(n["label"].lower(), {})
    os_slugs = list(dict.fromkeys([s for s in (lg.get("slug"), slug, slug.replace("-", ""), slug + "-nft") if s]))
    for s in os_slugs:
        j = http(f"https://api.opensea.io/api/v2/collections/{s}", headers={"x-api-key": OS_KEY} if OS_KEY else None, src="opensea")
        img = (j or {}).get("image_url")
        if img: yield "opensea", img; break
    else:
        yield "opensea", None
    j = http(f"https://api.coingecko.com/api/v3/nfts/{slug}", headers={"x-cg-demo-api-key": CG_KEY} if CG_KEY else None, src="coingecko-nft")
    img = ((j or {}).get("image") or {}).get("small_2x") or ((j or {}).get("image") or {}).get("small")
    if not img:  # look the collection up by name
        sr = http("https://api.coingecko.com/api/v3/search?query=" + urllib.parse.quote(n["label"].split(" (")[0]), src="coingecko-nft", pause=2)
        hit = next((x for x in (sr or {}).get("nfts", []) if x["name"].lower().startswith(n["label"].lower().split(" (")[0].split(".")[0])), None)
        if hit:
            j = http(f"https://api.coingecko.com/api/v3/nfts/{hit['id']}", src="coingecko-nft", pause=2)
            img = ((j or {}).get("image") or {}).get("small_2x") or ((j or {}).get("image") or {}).get("small")
    yield "coingecko-nft", img
    if n.get("chain") == "solana":
        for sym in (slug.replace("-", "_"), slug.replace("-classic", "").replace("-", "_")):
            j = http(f"https://api-mainnet.magiceden.dev/v2/collections/{sym}", src="magiceden", pause=1.5)
            if (j or {}).get("image"): yield "magiceden", j["image"]; break
        else:
            yield "magiceden", None
    j = http(f"https://api.reservoir.tools/collections/v7?slug={slug}", src="reservoir")
    yield "reservoir", (((j or {}).get("collections") or [{}])[0] or {}).get("image")

PRIOR = {}
prev_path = OUT / "icons.json"
if prev_path.exists():
    try: PRIOR = json.loads(prev_path.read_text())
    except Exception: PRIOR = {}

def kept_icon(kind, key):
    bucket = "coins" if kind == "coin" else "nfts"
    entry = (PRIOR.get(bucket) or {}).get(key)
    if not entry or entry.get("source") == "monogram": return None
    src = entry.get("src") or ""
    path = SITE / src
    if path.is_file() and path.stat().st_size > 0:
        return {"src": src, "source": entry.get("source"), "origin": entry.get("origin")}
    return None

def resolve(kind, key, label, gen):
    name = f"{kind}-{safe(key)}"
    kept = kept_icon(kind, key)
    if kept and not REFRESH: return kept
    try:
        for src, img in gen:
            if img is None: stat(src, ok=False); continue
            f = save_data_uri(img, name) if str(img).startswith("data:") else save_image(img, name)
            stat(src, ok=bool(f))
            if not f: stat(src, err="image download failed")
            if f: return {"src": "dashboard/icons/" + f, "source": src, "origin": img if not str(img).startswith("data:") else "inline-metadata"}
    except Exception as e:
        stat("resolve", err=type(e).__name__)
    if kept: return kept
    return {"src": "dashboard/icons/" + monogram(label, name, key), "source": "monogram", "origin": None}

out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "note": "Built by tools/resolve-icons.py. Browser loads local files only.",
       "coins": {}, "nfts": {}, "chains": {}}
for t, c in sorted(coins.items()):
    out["coins"][t] = {"chain": c.get("chain"), **resolve("coin", t, t, coin_sources(t, c))}
    print(f"coin {t:10s} -> {out['coins'][t]['source']}")
for slug, n in nfts.items():
    out["nfts"][slug] = {"name": n["label"], "chain": n.get("chain"), **resolve("nft", slug, n["label"], nft_sources(slug, n))}
    print(f"nft  {slug:26s} -> {out['nfts'][slug]['source']}")
# names → slug aliases so panels can look up by display name too
out["nft_names"] = {v["name"].lower(): k for k, v in out["nfts"].items()}
for ch in ("ethereum", "polygon", "solana", "base", "cardano", "bitcoin", "multi"):
    out["chains"][ch] = f"dashboard/icons/chains/{ch}.svg"
hits = lambda d: sum(1 for v in d.values() if v["source"] != "monogram")
out["summary"] = {"coins": f"{hits(out['coins'])}/{len(out['coins'])}", "nfts": f"{hits(out['nfts'])}/{len(out['nfts'])}", "sources": stats}
tmp = OUT / "icons.json.tmp"
tmp.write_text(json.dumps(out, indent=1))
tmp.replace(OUT / "icons.json")
print(json.dumps(out["summary"], indent=1))
