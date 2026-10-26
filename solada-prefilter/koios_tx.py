"""Shared Koios tx reader (review fixes S5/S8/S9/S10 + 1 KB clamp) for big_moves, whale_ledger and flags.

- /tx_info (not the deprecated /tx_utxos) with _inputs/_assets/_scripts: inputs exclude collateral and
  reference inputs (Koios lists those separately), outputs carry inline datums (used for order-replace detection).
- Batches are clamped so every POST body stays under the public tier's 1 KB limit (a 1,716 B body = HTTP 413).
- 404s are not silent (quiet404=False): a missing endpoint shows up in the data-failure log.
"""
import json, os

KOIOS = "https://api.koios.rest/api/v1"
BODY_MAX = 1000  # public tier rejects bodies over 1 KB


def batches(items, key, n, extra=None, body_max=BODY_MAX):
    """Split items into POST bodies {key: chunk, **extra} of <= n items and <= body_max bytes each."""
    out, cur = [], []
    for it in items:
        trial = cur + [it]
        if cur and (len(trial) > max(1, int(n)) or len(json.dumps({key: trial, **(extra or {})})) > body_max):
            out.append(cur)
            trial = [it]
        cur = trial
    if cur:
        out.append(cur)
    return [{key: c, **(extra or {})} for c in out]


def _datum_text(u):
    d = u.get("inline_datum")
    if isinstance(d, dict):
        return json.dumps(d.get("value") if d.get("value") is not None else d.get("bytes"), sort_keys=True)
    return ""


def norm_utxo(u):
    return {"ref": f"{u['tx_hash']}#{u['tx_index']}", "addr": (u.get("payment_addr") or {}).get("bech32") or "",
            "stake": u.get("stake_addr"), "lovelace": int(u.get("value") or 0),
            "assets": {a["policy_id"] + a["asset_name"]: int(a["quantity"]) for a in (u.get("asset_list") or [])},
            "datum": _datum_text(u)}


TX_INFO_FLAGS = {"_inputs": True, "_assets": True, "_scripts": True}


def tx_info(http, hashes, n, budget_ok=lambda: True):
    """{hash: {inputs, outputs}} with normalized UTxO dicts (spend inputs only). Missing hashes = not read."""
    out = {}
    for body in batches(hashes, "_tx_hashes", n, TX_INFO_FLAGS):
        if not budget_ok():
            break
        r = http.req("koios", KOIOS + "/tx_info", method="POST", body=body, quiet404=False)
        for tx in r or []:
            out[tx["tx_hash"]] = {"inputs": [norm_utxo(u) for u in tx.get("inputs") or []],
                                  "outputs": sorted((norm_utxo(u) for u in tx.get("outputs") or []),
                                                    key=lambda x: int(x["ref"].split("#")[1]))}
    return out


def write_json(path, obj, **kw):
    """Atomic write: temp file in the same directory, then os.replace."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(obj, f, **kw)
    os.replace(tmp, path)


def read_json(path, default):
    """Load a cache/state file; a corrupt or truncated file resets to default (never crashes the run)."""
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, type(default)) else default
    except (ValueError, OSError):
        return default
